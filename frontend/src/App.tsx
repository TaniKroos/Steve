import { Route, BrowserRouter, Routes } from "react-router-dom";
import { LoginScreen } from "./components/LoginScreen";
import { AppShell } from "./pages/AppShell";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LoginScreen />} />
        <Route path="/app" element={<AppShell />} />
      </Routes>
    </BrowserRouter>
  );
}
