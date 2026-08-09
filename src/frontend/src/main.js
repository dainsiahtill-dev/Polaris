import { jsx as _jsx } from "react/jsx-runtime";
import { createRoot } from "react-dom/client";
import App from "./app/App";
import "./styles/index.css";
import { QueryProvider } from "./lib/queryClient";
const root = document.getElementById("root");
if (!root) {
    throw new Error("Root element missing");
}
createRoot(root).render(_jsx(QueryProvider, { children: _jsx(App, {}) }));
