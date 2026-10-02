import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./app/App";
import "./app/prototype.css";
import "./app/app.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
