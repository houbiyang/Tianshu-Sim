// 模型输出是 Markdown（旧 Streamlit 用 st.markdown 渲染）。
// 这里做最小安全子集：先转义 HTML，再渲染标题/加粗/行内代码/列表，
// 保证 ** 与 ## 不会像纯文本那样裸露在战报里。
export function mdToHtml(text: string): string {
  const esc = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return esc
    .replace(/^#{2,4} (.*)$/gm, "<h4>$1</h4>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/^[-*] (.*)$/gm, "• $1")
    .replace(/\n/g, "<br/>");
}
