/** Ant Design 主题。色板与 web_next/src/tokens.js 同一套。 */

import { color, fontFamily, radius, shadow } from "./tokens.js";

export const antdTheme = {
  token: {
    colorPrimary: color.jade,
    colorInfo: color.jade,
    colorSuccess: color.success,
    colorWarning: color.warning,
    colorError: color.danger,
    colorText: color.text,
    colorTextSecondary: color.secondary,
    colorTextTertiary: color.tertiary,
    colorBgLayout: color.page,
    colorBgContainer: color.panel,
    colorBgElevated: color.elevated,
    colorBorder: color.line,
    colorBorderSecondary: color.line,
    colorInfoBg: color.infoBg,
    colorInfoBgHover: color.jadeSoft,
    colorInfoBorder: color.infoBorder,
    colorWarningBg: color.warningBg,
    colorWarningBorder: color.warningBorder,
    colorErrorBg: color.errorBg,
    colorErrorBorder: color.errorBorder,
    colorSuccessBg: color.successBg,
    colorSuccessBorder: color.successBorder,
    borderRadius: radius.control,
    fontFamily,
    fontSize: 14,
    controlHeight: 36,
    boxShadow: shadow.float,
    boxShadowSecondary: shadow.hover,
  },
  components: {
    Layout: {
      headerBg: color.panel,
      headerColor: color.text,
      headerHeight: 52,
      siderBg: color.panel,
      bodyBg: color.page,
    },
    Menu: {
      itemBg: "transparent",
      itemSelectedBg: color.jadeSoft,
      itemSelectedColor: color.jadeDeep,
      itemHoverBg: color.wash,
      groupTitleColor: color.tertiary,
      itemBorderRadius: radius.control,
      iconSize: 18,
    },
    Card: {
      headerBg: "transparent",
      borderRadiusLG: radius.card,
    },
    Button: {
      primaryShadow: "none",
      borderRadius: radius.control,
      controlHeight: 36,
    },
    Input: {
      borderRadius: radius.control,
      activeShadow: "0 0 0 3px rgba(15, 110, 107, 0.16)",
    },
    Select: {
      borderRadius: radius.control,
    },
    Tag: {
      borderRadiusSM: radius.tag,
    },
    Modal: {
      borderRadiusLG: radius.card,
    },
    Drawer: {
      colorBgElevated: color.panel,
    },
    Segmented: {
      borderRadius: radius.pill,
      borderRadiusSM: radius.pill,
      itemSelectedBg: color.elevated,
    },
    Dropdown: {
      borderRadiusLG: radius.card,
    },
    Tooltip: {
      borderRadius: radius.tag,
    },
  },
};
