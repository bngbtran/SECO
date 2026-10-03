import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import AdminLayout from "../features/admin/AdminLayout";
import LoginPage from "../features/admin/LoginPage";
import ResetPasswordPage from "../features/admin/ResetPasswordPage";
import PayPage from "../features/guest/PayPage";
import DashboardPage from "../features/admin/pages/DashboardPage";
import FaultsPage from "../features/admin/pages/FaultsPage";
import MaintenancePage from "../features/admin/pages/MaintenancePage";
import SessionDetailPage from "../features/admin/pages/SessionDetailPage";
import SessionsPage from "../features/admin/pages/SessionsPage";
import StationDetailPage from "../features/admin/pages/StationDetailPage";
import StationsPage from "../features/admin/pages/StationsPage";
import TransactionsPage from "../features/admin/pages/TransactionsPage";
import UserDetailPage from "../features/admin/pages/UserDetailPage";
import UsersPage from "../features/admin/pages/UsersPage";
import { AuthProvider, useAuth } from "../shared/auth";
import { RealtimeProvider } from "../shared/realtime";
import { ToastProvider } from "../shared/ui";

function Protected() {
  const { user, token, ready } = useAuth();
  if (!ready) return <div className="empty">Đang tải…</div>;
  if (!user || !token) return <Navigate to="/login" replace />;

  return (
    <RealtimeProvider token={token}>
      <AdminLayout />
    </RealtimeProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            {/* Guest sandbox payment, opened from the station QR: no login. */}
            <Route path="/pay/:paymentId" element={<PayPage />} />
            <Route element={<Protected />}>
              <Route index element={<DashboardPage />} />
              <Route path="stations" element={<StationsPage />} />
              <Route path="stations/:stationId" element={<StationDetailPage />} />
              <Route path="users" element={<UsersPage />} />
              <Route path="users/:userId" element={<UserDetailPage />} />
              <Route path="sessions" element={<SessionsPage />} />
              <Route path="sessions/:sessionId" element={<SessionDetailPage />} />
              <Route path="transactions" element={<TransactionsPage />} />
              <Route path="faults" element={<FaultsPage />} />
              <Route path="maintenance" element={<MaintenancePage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
