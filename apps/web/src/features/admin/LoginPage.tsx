import { Zap } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../../shared/auth";

export default function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      const message = (err as Error).message;
      setError(
        message === "Admin account required"
          ? "Tài khoản này không phải ADMIN. Member và kỹ thuật viên dùng ứng dụng di động."
          : message === "Invalid credentials"
            ? "Email hoặc mật khẩu không đúng."
            : message,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="login-hero">
        <div className="brand" style={{ color: "#fff" }}>
          <div className="brand-mark" style={{ background: "#fff", color: "var(--primary)" }}>
            <Zap size={18} />
          </div>
          SECO Admin
        </div>
        <div style={{ position: "relative", zIndex: 1 }}>
          <h2 style={{ fontSize: 30, lineHeight: 1.2, margin: "0 0 12px", letterSpacing: "-.02em", maxWidth: "18ch" }}>
            Theo dõi mọi trạm sạc, mọi phiên, theo thời gian thực.
          </h2>
          <p style={{ opacity: 0.8, maxWidth: "44ch", margin: 0 }}>
            Giám sát trạng thái trạm qua MQTT, quản lý người dùng và thẻ RFID, đối soát giao dịch và xử lý sự cố từ
            một nơi.
          </p>
        </div>
        <div style={{ display: "flex", gap: 26, fontSize: 12.5, opacity: 0.85 }}>
          <div>
            <b style={{ fontSize: 20, display: "block" }}>ESP32-S3</b>trạm mô phỏng Wokwi
          </div>
          <div>
            <b style={{ fontSize: 20, display: "block" }}>MQTT</b>telemetry realtime
          </div>
          <div>
            <b style={{ fontSize: 20, display: "block" }}>v1.2</b>design baseline
          </div>
        </div>
        <svg
          viewBox="0 0 400 400"
          style={{ position: "absolute", right: -90, top: 60, width: 420, opacity: 0.09 }}
          aria-hidden
        >
          <path d="M220 20 L90 220 H190 L160 380 L320 150 H215 Z" fill="#fff" />
        </svg>
      </div>

      <div className="login-form">
        <form style={{ width: "min(360px,100%)" }} onSubmit={submit}>
          <h1 style={{ margin: "0 0 6px", fontSize: 24, letterSpacing: "-.02em" }}>Đăng nhập quản trị</h1>
          <p className="muted" style={{ margin: "0 0 26px" }}>
            Dành cho tài khoản ADMIN.
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
              placeholder="ten@seco.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label className="label" htmlFor="password">
              Mật khẩu
            </label>
            <input
              id="password"
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <div style={{ textAlign: "right", margin: "-6px 0 12px" }}>
            <button type="button" className="link" style={{ fontSize: 12.5 }} onClick={() => setForgot(!forgot)}>
              Quên mật khẩu?
            </button>
          </div>
          {forgot && (
            <div className="hint" style={{ margin: "0 0 14px", padding: 12, border: "1px solid var(--border)", borderRadius: 10 }}>
              Liên hệ một quản trị viên khác để được cấp <b>mật khẩu tạm</b> (Người dùng → chi tiết tài khoản → Cấp mật
              khẩu tạm), rồi{" "}
              <Link className="link" to="/reset-password">
                đặt lại mật khẩu
              </Link>
              .
            </div>
          )}
          {error && (
            <div className="err" style={{ marginBottom: 12 }}>
              {error}
            </div>
          )}
          <button className="btn btn-primary btn-lg w-full" disabled={busy}>
            {busy ? "Đang đăng nhập…" : "Đăng nhập"}
          </button>
          <p className="hint" style={{ textAlign: "center", marginTop: 16 }}>
            Tài khoản USER / TECHNICIAN dùng ứng dụng di động.
          </p>
        </form>
      </div>
    </div>
  );
}
