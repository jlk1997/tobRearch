/** 暖纸令牌。CSS 变量见 tokens.css，两边保持同一组值。 */

export const color = {
  page: "#F6F4EF",
  panel: "#FFFDF9",
  elevated: "#FFFFFF",
  wash: "#EFEBE3",
  line: "#E6E0D5",
  lineStrong: "#D6CEC0",
  text: "#1C1917",
  secondary: "#57534E",
  tertiary: "#78716C",
  jade: "#0F6E6B",
  jadeDeep: "#0B5956",
  jadeSoft: "#EAF4F3",
  jadeEdge: "#CFE5E3",
  success: "#3F6F54",
  warning: "#C47B17",
  danger: "#B42318",
  gold: "#C4A35A",
  infoBg: "#E8F3F2",
  infoBorder: "#B7D4D2",
  warningBg: "#FBF3E4",
  warningBorder: "#E8D4A8",
  errorBg: "#FBECEA",
  errorBorder: "#E8B4B0",
  successBg: "#E8F3EC",
  successBorder: "#B7D4C2",
};

export const radius = {
  tag: 6,
  control: 10,
  card: 14,
  sheet: 20,
  pill: 999,
};

export const shadow = {
  hover: "0 1px 2px rgba(28, 25, 23, 0.04), 0 8px 20px rgba(28, 25, 23, 0.06)",
  float: "0 12px 32px rgba(28, 25, 23, 0.1)",
  palette: "0 24px 60px rgba(28, 25, 23, 0.16)",
};

export const fontFamily =
  '"Inter Variable", "Inter", "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "Segoe UI", sans-serif';

export function shortName(name, max = 24) {
  const text = String(name || "").trim();
  if (!text) return "";
  if (text.length <= max) return text;
  const head = text.split(/[:：]/)[0].trim();
  if (head.length >= 4 && head.length < text.length && head.length <= max) return head;
  return `${text.slice(0, max)}…`;
}
