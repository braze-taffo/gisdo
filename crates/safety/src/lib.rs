use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::fs;
use std::path::{Component, Path, PathBuf};

use chrono::Local;
use gisdo_domain::{PlanStep, TaskPlan, ValidationPolicy};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SafetyError {
    #[error("计划步骤 id 重复：{0}")]
    DuplicateStep(String),
    #[error("步骤 {step} 依赖不存在的步骤 {dependency}")]
    MissingDependency { step: String, dependency: String },
    #[error("计划包含循环依赖")]
    DependencyCycle,
    #[error("未知或未开放的工具：{0}")]
    UnknownTool(String),
    #[error("工具 {tool} 必须使用 {expected:?} 运行时，实际为 {actual:?}")]
    WrongRuntime {
        tool: String,
        expected: gisdo_domain::RuntimeKind,
        actual: gisdo_domain::RuntimeKind,
    },
    #[error("工具 {tool} 缺少必填参数：{parameter}")]
    MissingParameter { tool: String, parameter: String },
    #[error("输入不存在：{0}")]
    MissingInput(PathBuf),
    #[error("输出路径已存在，拒绝覆盖：{0}")]
    ExistingOutput(PathBuf),
    #[error("输出路径不是绝对路径：{0}")]
    RelativeOutput(PathBuf),
    #[error("输出路径包含父目录跳转：{0}")]
    ParentTraversal(PathBuf),
    #[error("计划 expected_outputs 与工具推导结果不一致")]
    OutputMismatch,
    #[error("计划包含重复校验工具 {0}；执行器会自动校验 count/CRS/extent")]
    RedundantValidation(String),
    #[error("GISdo 不执行删除操作：{0}；如需清理请手动处理对应文件")]
    DestructiveTool(String),
    #[error("计划不包含任何步骤")]
    EmptyPlan,
    #[error("无法读取工具清单：{0}")]
    Inventory(String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSpec {
    pub name: String,
    pub runtime: gisdo_domain::RuntimeKind,
    #[serde(default)]
    pub required_params: Vec<String>,
    #[serde(default)]
    pub input_params: Vec<String>,
    #[serde(default)]
    pub output_params: Vec<String>,
    #[serde(default)]
    pub write: bool,
    #[serde(default)]
    pub destructive: bool,
    #[serde(default)]
    pub default_validation: ValidationPolicy,
}

#[derive(Debug, Clone, Default)]
pub struct ToolRegistry {
    tools: HashMap<String, ToolSpec>,
}

impl ToolRegistry {
    pub fn new(specs: impl IntoIterator<Item = ToolSpec>) -> Self {
        Self {
            tools: specs
                .into_iter()
                .map(|spec| (spec.name.clone(), spec))
                .collect(),
        }
    }

    pub fn builtin() -> Self {
        use gisdo_domain::RuntimeKind::{Arcmap, Native, Pro};
        let specs = [
            (
                "inspect_aprx",
                Pro,
                vec!["project"],
                vec!["project"],
                vec![],
                false,
                ValidationPolicy::None,
            ),
            (
                "inspect_gdb",
                Pro,
                vec!["workspace"],
                vec!["workspace"],
                vec![],
                false,
                ValidationPolicy::None,
            ),
            (
                "inspect_mxd",
                Arcmap,
                vec!["project"],
                vec!["project"],
                vec![],
                false,
                ValidationPolicy::None,
            ),
            (
                "extract_data",
                Pro,
                vec!["project", "output"],
                vec!["project"],
                vec!["output"],
                true,
                ValidationPolicy::Dataset,
            ),
            (
                "package_project",
                Pro,
                vec!["project", "output"],
                vec!["project"],
                vec!["output"],
                true,
                ValidationPolicy::Package,
            ),
            (
                "export_legacy_lines",
                Arcmap,
                vec!["input", "output"],
                vec!["input"],
                vec!["output"],
                true,
                ValidationPolicy::Dataset,
            ),
            (
                "render_classified",
                Native,
                vec!["input", "output"],
                vec!["input"],
                vec!["output"],
                true,
                ValidationPolicy::Png,
            ),
            (
                "verify_png",
                Native,
                vec!["input"],
                vec!["input"],
                vec![],
                false,
                ValidationPolicy::Png,
            ),
        ]
        .into_iter()
        .map(
            |(name, runtime, required, inputs, outputs, write, validation)| ToolSpec {
                name: name.into(),
                runtime,
                required_params: required.into_iter().map(str::to_owned).collect(),
                input_params: inputs.into_iter().map(str::to_owned).collect(),
                output_params: outputs.into_iter().map(str::to_owned).collect(),
                write,
                destructive: false,
                default_validation: validation,
            },
        );
        Self::new(specs)
    }

    pub fn with_arcgis_inventory(mut self, inventory_json: &str) -> Result<Self, SafetyError> {
        let root: Value = serde_json::from_str(inventory_json)
            .map_err(|e| SafetyError::Inventory(e.to_string()))?;
        let data = root.get("data").unwrap_or(&root);
        let boxes = data
            .get("toolboxes")
            .and_then(Value::as_object)
            .ok_or_else(|| SafetyError::Inventory("缺少 toolboxes".into()))?;
        for (toolbox, entries) in boxes {
            let Some(entries) = entries.as_array() else {
                continue;
            };
            for entry in entries {
                let Some(short_name) = entry.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let name = format!("{toolbox}.{short_name}");
                let params = entry
                    .get("params")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                let mut required = Vec::new();
                let mut inputs = Vec::new();
                let mut outputs = Vec::new();
                for param in params {
                    let Some(param_name) = param.get("name").and_then(Value::as_str) else {
                        continue;
                    };
                    match param.get("direction").and_then(Value::as_str) {
                        Some("Input") => {
                            if is_path_like_datatype(param.get("datatype")) {
                                inputs.push(param_name.to_owned());
                            }
                            if param
                                .get("required")
                                .and_then(Value::as_bool)
                                .unwrap_or(false)
                            {
                                required.push(param_name.to_owned());
                            }
                        }
                        Some("Output") => {
                            outputs.push(param_name.to_owned());
                            if param
                                .get("required")
                                .and_then(Value::as_bool)
                                .unwrap_or(false)
                            {
                                required.push(param_name.to_owned());
                            }
                        }
                        _ => {}
                    }
                }
                let write = !outputs.is_empty();
                let destructive = short_name.starts_with("Delete") || short_name == "TruncateTable";
                self.tools.insert(
                    name.clone(),
                    ToolSpec {
                        name,
                        runtime: gisdo_domain::RuntimeKind::Pro,
                        required_params: required,
                        input_params: inputs,
                        output_params: outputs,
                        write,
                        destructive,
                        default_validation: if write {
                            ValidationPolicy::Dataset
                        } else {
                            ValidationPolicy::None
                        },
                    },
                );
            }
        }
        Ok(self)
    }

    pub fn get(&self, name: &str) -> Option<&ToolSpec> {
        self.tools.get(name)
    }
    pub fn len(&self) -> usize {
        self.tools.len()
    }
    pub fn is_empty(&self) -> bool {
        self.tools.is_empty()
    }
    pub fn is_write(&self, name: &str) -> Option<bool> {
        self.get(name).map(|s| s.write)
    }

    pub fn validate_step(&self, step: &PlanStep, check_files: bool) -> Result<(), SafetyError> {
        if step.tool.eq_ignore_ascii_case("management.GetCount")
            || step.tool.eq_ignore_ascii_case("GetCount")
        {
            return Err(SafetyError::RedundantValidation(step.tool.clone()));
        }
        let spec = self
            .get(&step.tool)
            .ok_or_else(|| SafetyError::UnknownTool(step.tool.clone()))?;
        if spec.destructive {
            return Err(SafetyError::DestructiveTool(step.tool.clone()));
        }
        if spec.runtime != step.runtime {
            return Err(SafetyError::WrongRuntime {
                tool: step.tool.clone(),
                expected: spec.runtime,
                actual: step.runtime,
            });
        }
        let params = step.params.as_object();
        for required in &spec.required_params {
            if params
                .and_then(|p| p.get(required))
                .is_none_or(Value::is_null)
            {
                return Err(SafetyError::MissingParameter {
                    tool: step.tool.clone(),
                    parameter: required.clone(),
                });
            }
        }
        if check_files {
            for key in &spec.input_params {
                for path in parameter_paths(params.and_then(|p| p.get(key))) {
                    if !path.exists() {
                        return Err(SafetyError::MissingInput(path));
                    }
                }
            }
            for key in &spec.output_params {
                for path in parameter_paths(params.and_then(|p| p.get(key))) {
                    validate_new_output(&path)?;
                }
            }
        }
        Ok(())
    }

    pub fn inferred_outputs(&self, plan: &TaskPlan) -> Result<Vec<PathBuf>, SafetyError> {
        let mut outputs = BTreeSet::new();
        for step in &plan.steps {
            let spec = self
                .get(&step.tool)
                .ok_or_else(|| SafetyError::UnknownTool(step.tool.clone()))?;
            let params = step.params.as_object();
            for key in &spec.output_params {
                outputs.extend(parameter_paths(params.and_then(|p| p.get(key))));
            }
        }
        Ok(outputs.into_iter().collect())
    }

    pub fn inferred_inputs(&self, plan: &TaskPlan) -> Result<Vec<PathBuf>, SafetyError> {
        let mut inputs = BTreeSet::new();
        for step in &plan.steps {
            let spec = self
                .get(&step.tool)
                .ok_or_else(|| SafetyError::UnknownTool(step.tool.clone()))?;
            let params = step.params.as_object();
            for key in &spec.input_params {
                inputs.extend(parameter_paths(params.and_then(|values| values.get(key))));
            }
        }
        Ok(inputs.into_iter().collect())
    }
}

fn parameter_paths(value: Option<&Value>) -> Vec<PathBuf> {
    match value {
        Some(Value::String(path)) => vec![PathBuf::from(path)],
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(Value::as_str)
            .map(PathBuf::from)
            .collect(),
        _ => Vec::new(),
    }
}

/// ArcPy 参数 datatype 中表示文件系统数据/文件位置的类型；组件精确匹配。
const PATH_LIKE_DATATYPES: &[&str] = &[
    "要素图层",
    "要素类",
    "表视图",
    "表",
    "栅格数据集",
    "栅格图层",
    "镶嵌图层",
    "镶嵌数据集",
    "工作空间",
    "文件",
    "文件夹",
    "文本文件",
    "LAS 数据集",
    "LAS 数据集图层",
    "TIN 图层",
    "拓扑",
    "拓扑图层",
    "关系类",
    "地址定位器",
    "数据元素",
    "图层",
    "地图",
    "工具箱",
    "场景图层",
    "建筑场景图层",
    "建筑图层",
    "轨迹图层",
    "目录图层",
    "公共设施网络图层",
    "GeoDataServer",
    "数据集",
    "复合地理数据集",
    "要素集",
    "要素数据集",
    "体素图层",
];

/// 判断参数 datatype 是否指向文件系统位置。字符串可含 `|` 分隔多类型，
/// 数组同理；任一组件命中即按路径处理。缺失或为空按路径类处理（保守），
/// 未识别的非空类型按值处理——存在性由 Worker 执行期的活元数据校验兜底。
fn is_path_like_datatype(datatype: Option<&Value>) -> bool {
    let mut components: Vec<&str> = Vec::new();
    match datatype {
        Some(Value::String(text)) => {
            components.extend(text.split('|').map(str::trim));
        }
        Some(Value::Array(items)) => {
            for item in items {
                if let Some(text) = item.as_str() {
                    components.extend(text.split('|').map(str::trim));
                }
            }
        }
        _ => return true,
    }
    components.retain(|component| !component.is_empty());
    if components.is_empty() {
        return true;
    }
    components
        .iter()
        .any(|component| PATH_LIKE_DATATYPES.contains(component))
}

pub fn validate_new_output(path: &Path) -> Result<(), SafetyError> {
    if !path.is_absolute() {
        return Err(SafetyError::RelativeOutput(path.to_owned()));
    }
    if path
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        return Err(SafetyError::ParentTraversal(path.to_owned()));
    }
    if path.exists() {
        return Err(SafetyError::ExistingOutput(path.to_owned()));
    }
    Ok(())
}

pub fn versioned_output(parent: &Path, stem: &str, extension: Option<&str>) -> PathBuf {
    let date = Local::now().format("%Y%m%d");
    for version in 1_u32.. {
        let mut name = format!("{stem}_v{version}_{date}");
        if let Some(ext) = extension.filter(|e| !e.is_empty()) {
            name.push('.');
            name.push_str(ext.trim_start_matches('.'));
        }
        let candidate = parent.join(name);
        if !candidate.exists() {
            return candidate;
        }
    }
    unreachable!("u32 output versions exhausted")
}

pub fn validate_plan(
    plan: &TaskPlan,
    registry: &ToolRegistry,
    check_files: bool,
) -> Result<Vec<String>, SafetyError> {
    if plan.steps.is_empty() {
        return Err(SafetyError::EmptyPlan);
    }
    let mut indegree: BTreeMap<String, usize> = BTreeMap::new();
    let mut dependents: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for step in &plan.steps {
        if indegree
            .insert(step.id.clone(), step.depends_on.len())
            .is_some()
        {
            return Err(SafetyError::DuplicateStep(step.id.clone()));
        }
        registry.validate_step(step, false)?;
    }
    for step in &plan.steps {
        for dependency in &step.depends_on {
            if !indegree.contains_key(dependency) {
                return Err(SafetyError::MissingDependency {
                    step: step.id.clone(),
                    dependency: dependency.clone(),
                });
            }
            dependents
                .entry(dependency.clone())
                .or_default()
                .push(step.id.clone());
        }
    }
    let mut queue: VecDeque<String> = indegree
        .iter()
        .filter(|(_, d)| **d == 0)
        .map(|(id, _)| id.clone())
        .collect();
    let mut ordered = Vec::with_capacity(plan.steps.len());
    while let Some(id) = queue.pop_front() {
        ordered.push(id.clone());
        for dependent in dependents.get(&id).into_iter().flatten() {
            let degree = indegree.get_mut(dependent).expect("known dependent");
            *degree -= 1;
            if *degree == 0 {
                queue.push_back(dependent.clone());
            }
        }
    }
    if ordered.len() != plan.steps.len() {
        return Err(SafetyError::DependencyCycle);
    }
    let inferred: BTreeSet<_> = registry.inferred_outputs(plan)?.into_iter().collect();
    let declared: BTreeSet<_> = plan.expected_outputs.iter().cloned().collect();
    if inferred != declared {
        return Err(SafetyError::OutputMismatch);
    }
    if check_files {
        for output in &inferred {
            validate_new_output(output)?;
        }
        for input in registry.inferred_inputs(plan)? {
            if !input.exists() && !inferred.contains(&input) {
                return Err(SafetyError::MissingInput(input));
            }
        }
    }
    Ok(ordered)
}

pub fn canonical_plan_json(plan: &TaskPlan) -> Result<Vec<u8>, serde_json::Error> {
    let value = serde_json::to_value(plan)?;
    serde_json::to_vec(&canonicalize(value))
}

fn canonicalize(value: Value) -> Value {
    match value {
        Value::Object(map) => {
            let ordered: BTreeMap<_, _> =
                map.into_iter().map(|(k, v)| (k, canonicalize(v))).collect();
            Value::Object(ordered.into_iter().collect())
        }
        Value::Array(values) => Value::Array(values.into_iter().map(canonicalize).collect()),
        other => other,
    }
}

pub fn plan_hash(plan: &TaskPlan) -> Result<String, serde_json::Error> {
    Ok(hex::encode(Sha256::digest(canonical_plan_json(plan)?)))
}

pub fn sha256_file(path: &Path) -> std::io::Result<String> {
    Ok(hex::encode(Sha256::digest(fs::read(path)?)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use gisdo_domain::{RuntimeKind, TaskPlan};
    use serde_json::json;
    use uuid::Uuid;

    fn step(id: &str, depends_on: &[&str]) -> PlanStep {
        PlanStep {
            id: id.into(),
            stage: None,
            requirement_refs: vec![],
            runtime: RuntimeKind::Native,
            tool: "verify_png".into(),
            params: json!({"input": "C:\\missing.png"}),
            depends_on: depends_on.iter().map(|s| (*s).into()).collect(),
            validation: ValidationPolicy::Png,
        }
    }

    fn plan(steps: Vec<PlanStep>) -> TaskPlan {
        TaskPlan {
            version: 1,
            id: Uuid::nil(),
            goal: "test".into(),
            steps,
            expected_outputs: vec![],
        }
    }

    #[test]
    fn stable_hash_ignores_object_key_order() {
        let mut a = plan(vec![step("a", &[])]);
        let mut b = a.clone();
        a.steps[0].params = json!({"input": "x", "b": 2, "a": 1});
        b.steps[0].params = json!({"a": 1, "input": "x", "b": 2});
        assert_eq!(plan_hash(&a).unwrap(), plan_hash(&b).unwrap());
    }

    #[test]
    fn rejects_cycles() {
        let err = validate_plan(
            &plan(vec![step("a", &["b"]), step("b", &["a"])]),
            &ToolRegistry::builtin(),
            false,
        )
        .unwrap_err();
        assert!(matches!(err, SafetyError::DependencyCycle));
    }

    #[test]
    fn sorts_dag() {
        let order = validate_plan(
            &plan(vec![step("b", &["a"]), step("a", &[])]),
            &ToolRegistry::builtin(),
            false,
        )
        .unwrap();
        assert_eq!(order, ["a", "b"]);
    }

    #[test]
    fn allows_a_dependency_output_as_a_later_input() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source.png");
        fs::write(&source, b"source").unwrap();
        let output = temp.path().join("output.png");
        let mut registry = ToolRegistry::builtin();
        registry.tools.insert(
            "native.copy".into(),
            ToolSpec {
                name: "native.copy".into(),
                runtime: RuntimeKind::Native,
                required_params: vec!["input".into(), "output".into()],
                input_params: vec!["input".into()],
                output_params: vec!["output".into()],
                write: true,
                destructive: false,
                default_validation: ValidationPolicy::None,
            },
        );
        let task = TaskPlan {
            version: 1,
            id: Uuid::nil(),
            goal: "pipeline".into(),
            expected_outputs: vec![output.clone()],
            steps: vec![
                PlanStep {
                    id: "write".into(),
                    stage: None,
                    requirement_refs: vec![],
                    runtime: RuntimeKind::Native,
                    tool: "native.copy".into(),
                    params: json!({"input":source,"output":output}),
                    depends_on: vec![],
                    validation: ValidationPolicy::None,
                },
                PlanStep {
                    id: "read".into(),
                    stage: None,
                    requirement_refs: vec![],
                    runtime: RuntimeKind::Native,
                    tool: "verify_png".into(),
                    params: json!({"input":output}),
                    depends_on: vec!["write".into()],
                    validation: ValidationPolicy::Png,
                },
            ],
        };
        assert!(validate_plan(&task, &registry, true).is_ok());
    }

    const INVENTORY_JSON: &str = include_str!("../../../fixtures/arcgis_tool_inventory_510.json");

    fn inventory_registry() -> ToolRegistry {
        ToolRegistry::builtin()
            .with_arcgis_inventory(INVENTORY_JSON)
            .unwrap()
    }

    fn pro_step(id: &str, tool: &str, params: Value) -> PlanStep {
        PlanStep {
            id: id.into(),
            stage: None,
            requirement_refs: vec![],
            runtime: RuntimeKind::Pro,
            tool: tool.into(),
            params,
            depends_on: vec![],
            validation: ValidationPolicy::Dataset,
        }
    }

    #[test]
    fn value_like_input_params_are_not_path_checked() {
        let registry = inventory_registry();
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("roads.shp");
        fs::write(&source, b"source").unwrap();
        let output = temp.path().join("roads_buffer.shp");
        let buffer = pro_step(
            "buffer",
            "analysis.Buffer",
            json!({
                "in_features": source,
                "out_feature_class": output,
                "buffer_distance_or_field": "100 Meters"
            }),
        );
        assert!(registry.validate_step(&buffer, true).is_ok());

        let table = temp.path().join("buildings.dbf");
        fs::write(&table, b"table").unwrap();
        let calc = pro_step(
            "calc",
            "management.CalculateField",
            json!({
                "in_table": table,
                "field": "POP",
                "expression": "!POP! * 2"
            }),
        );
        assert!(registry.validate_step(&calc, true).is_ok());
    }

    #[test]
    fn path_like_inputs_still_require_existence() {
        let registry = inventory_registry();
        let temp = tempfile::tempdir().unwrap();
        let step = pro_step(
            "buffer",
            "analysis.Buffer",
            json!({
                "in_features": temp.path().join("missing.shp"),
                "out_feature_class": temp.path().join("out.shp"),
                "buffer_distance_or_field": "100 Meters"
            }),
        );
        let err = registry.validate_step(&step, true).unwrap_err();
        assert!(matches!(err, SafetyError::MissingInput(_)));
    }

    #[test]
    fn destructive_tools_are_rejected() {
        let registry = inventory_registry();
        let temp = tempfile::tempdir().unwrap();
        let victim = temp.path().join("victim.shp");
        fs::write(&victim, b"victim").unwrap();
        for tool in [
            "management.Delete",
            "management.DeleteFeatures",
            "management.TruncateTable",
        ] {
            let step = pro_step(
                "delete",
                tool,
                json!({"in_data": victim, "in_features": victim, "in_table": victim}),
            );
            let err = registry.validate_step(&step, false).unwrap_err();
            assert!(
                matches!(err, SafetyError::DestructiveTool(_)),
                "{tool}: {err:?}"
            );
        }
    }

    #[test]
    fn empty_plan_is_rejected() {
        let err = validate_plan(&plan(vec![]), &ToolRegistry::builtin(), false).unwrap_err();
        assert!(matches!(err, SafetyError::EmptyPlan));
    }

    #[test]
    fn datatype_classification_matches_path_components_exactly() {
        assert!(is_path_like_datatype(Some(&json!("要素图层"))));
        assert!(is_path_like_datatype(Some(&json!(
            "要素图层|场景图层|文件"
        ))));
        assert!(is_path_like_datatype(Some(&json!(["表视图", "栅格图层"]))));
        assert!(!is_path_like_datatype(Some(&json!("SQL 表达式"))));
        assert!(!is_path_like_datatype(Some(&json!("字段"))));
        assert!(!is_path_like_datatype(Some(&json!(["线性单位", "字段"]))));
        assert!(is_path_like_datatype(None));
        assert!(is_path_like_datatype(Some(&json!(""))));
    }
}
