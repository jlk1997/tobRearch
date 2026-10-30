import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import "@fontsource-variable/inter";
import App from "./App.jsx";
import { queryClient } from "./query.js";
import { antdTheme } from "./theme.js";
import "./tokens.css";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={antdTheme}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
