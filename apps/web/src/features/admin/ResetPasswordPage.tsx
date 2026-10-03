import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { api } from "../../shared/api";
import { useAuth } from "../../shared/auth";
import type { LoginResult } from "../../shared/types";
import { MIN_PASSWORD, passwordError } from "./PasswordDialogs";

/** Set a new password with the temporary password another admin issued. */
export default function ResetPasswordPage() {
  const { user, applySession } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: "", temporary: "", next: "", confirm: "" });
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  if (user) return <Navigate to="/" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (form.next !== form.confirm) {
      setError("Mật khẩu nhập lại không khớp.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api<LoginResult>("/auth/reset-password", {
        method: "POST",
        body: { email: form.email, temporary_password: form.temporary.trim(), new_password: form.next },
      });
      if (result.user.role === "ADMIN") {
        applySession(result);
        navigate("/");
      } else {
        setDone(true); // members and technicians sign in on the mobile app
      }
    } catch (err) {
      setError(passwordError((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login" style={{ gridTemplateColumns: "1fr" }}>
      <div className="login-form">
        {done ? (
          <div style={{ width: "min(380px,100%)", textAlign: "center" }}>
            <h1 style={{ margin: "0 0 8px", fontSize: 22 }}>Đã đặt mật khẩu mới</h1>
            <p className="muted">Đăng nhập trên ứng dụng di động bằng mật khẩu mới.</p>
          </div>
        ) : (
          <form style={{ width: "min(380px,100%)" }} onSubmit={submit}>
            <div className="row" style={{ gap: 10, marginBottom: 6 }}>
              <span className="brand-mark" style={{ background: "var(--primary-50)", color: "var(--primary)" }}>
                <KeyRound size={18} />
              </span>
              <h1 style={{ margin: 0, fontSize: 24, letterSpacing: "-.02em" }}>Đặt lại mật khẩu</h1>
            </div>
            <p className="muted" style={{ margin: "0 0 22px" }}>
              Nhập mật khẩu tạm được quản trị viên cấp, như khi đổi mật khẩu.
            </p>
            <div className="field">
              <label className="label" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                className="input"
                type="email"
                autoComplete="username"
                value={form.email}
                onChange={set("email")}
                required
              />
            </div>
            <div className="field">
              <label className="label" htmlFor="temporary">
                Mật khẩu tạm
              </label>
              <input
                id="temporary"
                className="input mono"
                autoComplete="one-time-code"
                value={form.temporary}
                onChange={set("temporary")}
                required
              />
            </div>
            <div className="field">
              <label className="label" htmlFor="next">
                Mật khẩu mới
              </label>
              <input
                id="next"
                className="input"
                type="password"
                autoComplete="new-password"
                value={form.next}
                onChange={set("next")}
                minLength={MIN_PASSWORD}
                required
              />
              <div className="hint">Tối thiểu {MIN_PASSWORD} ký tự</div>
            </div>
            <div className="field">
              <label className="label" htmlFor="confirm">
                Nhập lại mật khẩu mới
              </label>
              <input
                id="confirm"
                className="input"
                type="password"
                autoComplete="new-password"
                value={form.confirm}
                onChange={set("confirm")}
                minLength={MIN_PASSWORD}
                required
              />
            </div>
            {error && (
              <div className="err" style={{ marginBottom: 12 }}>
                {error}
              </div>
            )}
            <button className="btn btn-primary btn-lg w-full" disabled={busy}>
              {busy ? "Đang lưu…" : "Đặt mật khẩu mới"}
            </button>
            <p className="hint" style={{ textAlign: "center", marginTop: 16 }}>
              <Link className="link" to="/login">
                Quay lại đăng nhập
              </Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
