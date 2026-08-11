import { render, screen } from "@testing-library/react";
import { MarkdownMessage } from "./components";

describe("MarkdownMessage", () => {
  it("renders one markdown presentation without duplicating raw text", () => {
    const { container } = render(<MarkdownMessage message={{ id: "1", role: "assistant", content: "## 汇报\n\n**完成**" }} />);
    expect(screen.getByRole("heading", { name: "汇报" })).toBeInTheDocument();
    expect(screen.getByText("完成")).toBeInTheDocument();
    expect(container.textContent?.match(/汇报/g)).toHaveLength(1);
    expect(container.querySelectorAll(".markdown-body")).toHaveLength(1);
  });
});
