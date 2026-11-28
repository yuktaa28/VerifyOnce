import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { Layout } from "./components/Layout";
import { SystemProvider } from "./components/status";
import { CaseView } from "./pages/CaseView";
import { Citizen } from "./pages/Citizen";
import { Console } from "./pages/Console";
import { HowItWorks } from "./pages/HowItWorks";
import { Landing } from "./pages/Landing";
import { NotFound } from "./pages/NotFound";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SystemProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Landing />} />
            <Route path="citizen" element={<Citizen />} />
            <Route path="console" element={<Console />} />
            <Route path="console/case/:id" element={<CaseView />} />
            <Route path="how-it-works" element={<HowItWorks />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SystemProvider>
  </StrictMode>,
);
