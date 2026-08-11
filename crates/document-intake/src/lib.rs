use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

use calamine::{Reader, open_workbook_auto};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use zip::ZipArchive;

pub const DEFAULT_MAX_DOCUMENTS: usize = 32;
pub const DEFAULT_MAX_DOCUMENT_CHARACTERS: usize = 80_000;
pub const DEFAULT_MAX_CORPUS_CHARACTERS: usize = 240_000;
const MAX_SOURCE_BYTES: u64 = 128 * 1024 * 1024;

const SUPPORTED_EXTENSIONS: &[&str] = &[
    "pdf", "docx", "pptx", "xlsx", "xls", "xlsm", "xlsb", "ods", "md", "txt", "csv", "json", "xml",
    "html", "htm",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedDocument {
    pub path: PathBuf,
    pub kind: String,
    pub sha256: String,
    pub source_bytes: u64,
    pub content_markdown: String,
    pub characters: usize,
    pub truncated: bool,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DocumentCorpus {
    pub documents: Vec<ExtractedDocument>,
    pub total_characters: usize,
    pub truncated: bool,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Error)]
pub enum DocumentError {
    #[error("无法读取文档 {path}: {message}")]
    Read { path: PathBuf, message: String },
    #[error("不支持的文档格式：{0}")]
    Unsupported(PathBuf),
}

pub fn is_supported_document(path: &Path) -> bool {
    path.extension()
        .and_then(|value| value.to_str())
        .map(str::to_lowercase)
        .is_some_and(|extension| SUPPORTED_EXTENSIONS.contains(&extension.as_str()))
}

pub fn discover_documents(roots: &[PathBuf], max_documents: usize) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut seen = BTreeSet::new();
    for root in roots {
        discover_from(root, 0, max_documents, &mut seen, &mut found);
        if found.len() >= max_documents {
            break;
        }
    }
    found.sort_by(|left, right| {
        left.to_string_lossy()
            .to_lowercase()
            .cmp(&right.to_string_lossy().to_lowercase())
    });
    found.truncate(max_documents);
    found
}

fn discover_from(
    path: &Path,
    depth: usize,
    limit: usize,
    seen: &mut BTreeSet<String>,
    found: &mut Vec<PathBuf>,
) {
    if found.len() >= limit || depth > 6 {
        return;
    }
    if path.is_file() {
        if is_supported_document(path) {
            let key = path.to_string_lossy().replace('/', "\\").to_lowercase();
            if seen.insert(key) {
                found.push(path.to_owned());
            }
        }
        return;
    }
    if !path.is_dir() {
        return;
    }
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    let mut entries: Vec<_> = entries.flatten().collect();
    entries.sort_by_key(|entry| entry.file_name().to_string_lossy().to_lowercase());
    for entry in entries {
        let child = entry.path();
        if child.is_dir()
            && child
                .file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|name| {
                    matches!(
                        name.to_lowercase().as_str(),
                        ".git" | "target" | "node_modules" | ".venv" | "__pycache__"
                    )
                })
        {
            continue;
        }
        discover_from(&child, depth + 1, limit, seen, found);
        if found.len() >= limit {
            return;
        }
    }
}

pub fn extract_corpus(
    roots: &[PathBuf],
    max_documents: usize,
    max_document_characters: usize,
    max_corpus_characters: usize,
) -> DocumentCorpus {
    let paths = discover_documents(roots, max_documents.saturating_add(1));
    let mut corpus = DocumentCorpus::default();
    if paths.len() > max_documents {
        corpus.truncated = true;
        corpus.warnings.push(format!(
            "文档数量超过 {max_documents}，只读取排序后的前 {max_documents} 份"
        ));
    }
    let mut remaining = max_corpus_characters;
    for path in paths.into_iter().take(max_documents) {
        if remaining == 0 {
            corpus.truncated = true;
            break;
        }
        match extract_document(&path, max_document_characters.min(remaining)) {
            Ok(document) => {
                remaining = remaining.saturating_sub(document.characters);
                corpus.total_characters += document.characters;
                corpus.truncated |= document.truncated;
                corpus.documents.push(document);
            }
            Err(error) => corpus.warnings.push(error.to_string()),
        }
    }
    corpus
}

pub fn extract_document(
    path: &Path,
    max_characters: usize,
) -> Result<ExtractedDocument, DocumentError> {
    let metadata = fs::metadata(path).map_err(|error| read_error(path, error))?;
    if metadata.len() > MAX_SOURCE_BYTES {
        return Err(DocumentError::Read {
            path: path.to_owned(),
            message: format!("文件超过 {} MB 安全上限", MAX_SOURCE_BYTES / 1024 / 1024),
        });
    }
    let bytes = fs::read(path).map_err(|error| read_error(path, error))?;
    let sha256 = hex_sha256(&bytes);
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_lowercase)
        .ok_or_else(|| DocumentError::Unsupported(path.to_owned()))?;
    let mut warnings = Vec::new();
    let content = match extension.as_str() {
        "pdf" => extract_pdf(path, &mut warnings)?,
        "docx" => extract_docx(path)?,
        "pptx" => extract_pptx(path)?,
        "xlsx" | "xls" | "xlsm" | "xlsb" | "ods" => extract_spreadsheet(path)?,
        "html" | "htm" | "xml" => strip_markup(&decode_text(&bytes)),
        "md" | "txt" | "csv" | "json" => decode_text(&bytes),
        _ => return Err(DocumentError::Unsupported(path.to_owned())),
    };
    let (content_markdown, truncated) = truncate_characters(content, max_characters);
    if truncated {
        warnings.push(format!(
            "正文超过 {max_characters} 字符，已截断模型上下文副本"
        ));
    }
    let characters = content_markdown.chars().count();
    Ok(ExtractedDocument {
        path: path.to_owned(),
        kind: extension,
        sha256,
        source_bytes: metadata.len(),
        content_markdown,
        characters,
        truncated,
        warnings,
    })
}

fn extract_pdf(path: &Path, warnings: &mut Vec<String>) -> Result<String, DocumentError> {
    let pages = pdf_extract::extract_text_by_pages(path).map_err(|error| DocumentError::Read {
        path: path.to_owned(),
        message: error.to_string(),
    })?;
    let mut output = String::new();
    for (index, page) in pages.into_iter().enumerate() {
        output.push_str(&format!("\n\n## 第 {} 页\n\n{}", index + 1, page.trim()));
    }
    if output
        .chars()
        .filter(|character| !character.is_whitespace())
        .count()
        < 32
    {
        warnings.push("PDF 没有足够的可提取文本，可能是扫描件，需要 OCR".into());
    }
    Ok(output.trim().to_owned())
}

fn extract_docx(path: &Path) -> Result<String, DocumentError> {
    let xml = read_zip_entry(path, "word/document.xml")?;
    Ok(extract_xml_text(&xml, "w:t", "w:p"))
}

fn extract_pptx(path: &Path) -> Result<String, DocumentError> {
    let file = File::open(path).map_err(|error| read_error(path, error))?;
    let mut archive = ZipArchive::new(file).map_err(|error| DocumentError::Read {
        path: path.to_owned(),
        message: error.to_string(),
    })?;
    let mut names: Vec<String> = archive
        .file_names()
        .filter(|name| name.starts_with("ppt/slides/slide") && name.ends_with(".xml"))
        .map(str::to_owned)
        .collect();
    names.sort_by_key(|name| numeric_suffix(name));
    let mut output = String::new();
    for (index, name) in names.into_iter().enumerate() {
        let mut xml = String::new();
        let mut entry = archive
            .by_name(&name)
            .map_err(|error| DocumentError::Read {
                path: path.to_owned(),
                message: error.to_string(),
            })?;
        entry
            .read_to_string(&mut xml)
            .map_err(|error| read_error(path, error))?;
        output.push_str(&format!(
            "\n\n## 幻灯片 {}\n\n{}",
            index + 1,
            extract_xml_text(&xml, "a:t", "a:p")
        ));
    }
    Ok(output.trim().to_owned())
}

fn extract_spreadsheet(path: &Path) -> Result<String, DocumentError> {
    let mut workbook = open_workbook_auto(path).map_err(|error| DocumentError::Read {
        path: path.to_owned(),
        message: error.to_string(),
    })?;
    let names = workbook.sheet_names();
    let mut output = String::new();
    for name in names {
        let range = workbook
            .worksheet_range(&name)
            .map_err(|error| DocumentError::Read {
                path: path.to_owned(),
                message: error.to_string(),
            })?;
        output.push_str(&format!("\n\n## 工作表：{}\n\n", escape_markdown(&name)));
        let mut rows = range.rows();
        let Some(first) = rows.next() else {
            output.push_str("（空工作表）\n");
            continue;
        };
        let width = first.len().min(40);
        let header: Vec<_> = first
            .iter()
            .take(width)
            .enumerate()
            .map(|(index, value)| {
                let rendered = value.to_string();
                let header = if rendered.trim().is_empty() {
                    format!("列{}", index + 1)
                } else {
                    rendered
                };
                escape_markdown(&header)
            })
            .collect();
        output.push_str(&format!("| {} |\n", header.join(" | ")));
        output.push_str(&format!("| {} |\n", vec!["---"; width].join(" | ")));
        for row in rows.take(2_000) {
            let cells: Vec<_> = (0..width)
                .map(|index| {
                    row.get(index)
                        .map(ToString::to_string)
                        .map(|value| escape_markdown(&value))
                        .unwrap_or_default()
                })
                .collect();
            output.push_str(&format!("| {} |\n", cells.join(" | ")));
        }
    }
    Ok(output.trim().to_owned())
}

fn read_zip_entry(path: &Path, name: &str) -> Result<String, DocumentError> {
    let file = File::open(path).map_err(|error| read_error(path, error))?;
    let mut archive = ZipArchive::new(file).map_err(|error| DocumentError::Read {
        path: path.to_owned(),
        message: error.to_string(),
    })?;
    let mut content = String::new();
    let mut entry = archive.by_name(name).map_err(|error| DocumentError::Read {
        path: path.to_owned(),
        message: error.to_string(),
    })?;
    entry
        .read_to_string(&mut content)
        .map_err(|error| read_error(path, error))?;
    Ok(content)
}

fn extract_xml_text(xml: &str, text_tag: &str, paragraph_tag: &str) -> String {
    let mut output = String::new();
    let close_text = format!("</{text_tag}>");
    let close_paragraph = format!("</{paragraph_tag}>");
    let mut cursor = 0;
    while let Some(open_offset) = xml[cursor..].find(&format!("<{text_tag}")) {
        let open = cursor + open_offset;
        let Some(content_start_offset) = xml[open..].find('>') else {
            break;
        };
        let content_start = open + content_start_offset + 1;
        let Some(close_offset) = xml[content_start..].find(&close_text) else {
            break;
        };
        let close = content_start + close_offset;
        output.push_str(&xml_unescape(&xml[content_start..close]));
        let next = close + close_text.len();
        let next_text = xml[next..]
            .find(&format!("<{text_tag}"))
            .map(|offset| next + offset)
            .unwrap_or(xml.len());
        if xml[next..next_text].contains(&close_paragraph) {
            output.push('\n');
        } else {
            output.push(' ');
        }
        cursor = next;
    }
    output.trim().to_owned()
}

fn strip_markup(source: &str) -> String {
    let mut output = String::with_capacity(source.len());
    let mut in_tag = false;
    for character in source.chars() {
        match character {
            '<' => in_tag = true,
            '>' => {
                in_tag = false;
                output.push(' ');
            }
            _ if !in_tag => output.push(character),
            _ => {}
        }
    }
    xml_unescape(&output)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn xml_unescape(value: &str) -> String {
    value
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&amp;", "&")
}

fn decode_text(bytes: &[u8]) -> String {
    if bytes.starts_with(&[0xFF, 0xFE]) {
        let words: Vec<u16> = bytes[2..]
            .chunks_exact(2)
            .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
            .collect();
        return String::from_utf16_lossy(&words);
    }
    if bytes.starts_with(&[0xFE, 0xFF]) {
        let words: Vec<u16> = bytes[2..]
            .chunks_exact(2)
            .map(|chunk| u16::from_be_bytes([chunk[0], chunk[1]]))
            .collect();
        return String::from_utf16_lossy(&words);
    }
    String::from_utf8_lossy(bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(bytes)).into_owned()
}

fn truncate_characters(mut value: String, max_characters: usize) -> (String, bool) {
    let Some((byte_index, _)) = value.char_indices().nth(max_characters) else {
        return (value, false);
    };
    value.truncate(byte_index);
    value.push_str("\n\n[GISdo：文档上下文已截断]");
    (value, true)
}

fn escape_markdown(value: &str) -> String {
    value.replace('|', "\\|").replace(['\r', '\n'], " ")
}

fn numeric_suffix(name: &str) -> u32 {
    name.rsplit_once("slide")
        .and_then(|(_, suffix)| suffix.strip_suffix(".xml"))
        .and_then(|value| value.parse().ok())
        .unwrap_or(u32::MAX)
}

fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn read_error(path: &Path, error: impl std::fmt::Display) -> DocumentError {
    DocumentError::Read {
        path: path.to_owned(),
        message: error.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;
    use tempfile::tempdir;
    use zip::write::SimpleFileOptions;

    #[test]
    fn extracts_utf8_and_utf16_text() {
        let temp = tempdir().unwrap();
        let utf8 = temp.path().join("路线图.md");
        fs::write(&utf8, "# 路线图\n输出三张地图").unwrap();
        let extracted = extract_document(&utf8, 10_000).unwrap();
        assert!(extracted.content_markdown.contains("三张地图"));

        let utf16 = temp.path().join("任务书.txt");
        let mut bytes = vec![0xFF, 0xFE];
        for word in "缓冲距离 500 米".encode_utf16() {
            bytes.extend_from_slice(&word.to_le_bytes());
        }
        fs::write(&utf16, bytes).unwrap();
        assert!(
            extract_document(&utf16, 10_000)
                .unwrap()
                .content_markdown
                .contains("500 米")
        );
    }

    #[test]
    fn extracts_docx_paragraphs_without_external_office() {
        let temp = tempdir().unwrap();
        let path = temp.path().join("项目任务书.docx");
        let file = File::create(&path).unwrap();
        let mut writer = zip::ZipWriter::new(file);
        writer
            .start_file("word/document.xml", SimpleFileOptions::default())
            .unwrap();
        writer
            .write_all(
                r#"<w:document><w:body><w:p><w:r><w:t>第一阶段</w:t></w:r></w:p><w:p><w:r><w:t>制作成果图</w:t></w:r></w:p></w:body></w:document>"#
                    .as_bytes(),
            )
            .unwrap();
        writer.finish().unwrap();
        let extracted = extract_document(&path, 10_000).unwrap();
        assert_eq!(extracted.content_markdown, "第一阶段\n制作成果图");
    }

    #[test]
    fn corpus_is_bounded_and_discovers_supported_files() {
        let temp = tempdir().unwrap();
        fs::write(temp.path().join("a.md"), "a".repeat(200)).unwrap();
        fs::write(temp.path().join("ignore.bin"), b"ignore").unwrap();
        let corpus = extract_corpus(&[temp.path().to_owned()], 8, 50, 50);
        assert_eq!(corpus.documents.len(), 1);
        assert!(corpus.documents[0].truncated);
        assert!(corpus.truncated);
    }
}
