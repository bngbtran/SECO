import { Check, Copy, KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../../shared/api";
import { useAuth } from "../../shared/auth";
import { fDate } from "../../shared/format";
import type { AdminUserDetail, LoginResult, TemporaryPassword } from "../../shared/types";
import { Dialog, Field, useToast } from "../../shared/ui";

export const MIN_PASSWORD = 6;

/** API error -> what the admin should read. */
export function passwordError(message: string) {
  return (
    {
      "Current password is incorrect": "Mật khẩu hiện tại không đúng.",
      "New password must be different from the current one": "Mật khẩu mới phải khác mật khẩu hiện tại.",
      "New password must be different from the temporary one": "Mật khẩu mới phải khác mật khẩu tạm.",
      "Invalid or expired temporary password": "Mật khẩu tạm không đúng hoặc đã hết hạn.",
      "Use change password for your own account": "Với tài khoản của bạn, hãy dùng Đổi mật khẩu.",
      "Account is locked; unlock it first": "Tài khoản đang bị khoá, hãy mở khoá trước.",
    }[message] ?? message
  );
}

export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const { applySession } = useAuth();
  const toast = useToast();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (form.next !== form.confirm) {
      setError("Mật khẩu nhập lại không khớp.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Every other device is signed out; this one continues with the new token.
      applySession(
        await api<LoginResult>("/auth/change-password", {
          method: "POST",
          body: { current_password: form.current, new_password: form.next },
        }),
      );
      toast("Đã đổi mật khẩu", "Các thiết bị khác đã được đăng xuất");
      onClose();
    } catch (err) {
      setError(passwordError((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="Đổi mật khẩu" subtitle="Tài khoản của bạn" icon={KeyRound} onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="Mật khẩu hiện tại">
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            value={form.current}
            onChange={set("current")}
            required
            autoFocus
          />
        </Field>
        <Field label="Mật khẩu mới" hint={`Tối thiểu ${MIN_PASSWORD} ký tự`}>
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            value={form.next}
            onChange={set("next")}
            minLength={MIN_PASSWORD}
            required
          />
        </Field>
        <Field label="Nhập lại mật khẩu mới">
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            value={form.confirm}
            onChange={set("confirm")}
            minLength={MIN_PASSWORD}
            required
          />
        </Field>
        {error && <div className="err">{error}</div>}
        <div className="df" style={{ padding: "8px 0 0" }}>
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Huỷ
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Đang lưu…" : "Đổi mật khẩu"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** Issue a temporary password for someone who forgot theirs, then show it once. */
export function TemporaryPasswordDialog({ user, onClose }: { user: AdminUserDetail; onClose: () => void }) {
  const [issued, setIssued] = useState<TemporaryPassword | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const issue = async () => {
    setBusy(true);
    setError(null);
    try {
      setIssued(await api<TemporaryPassword>(`/admin/users/${user.user_id}/temporary-password`, { method: "POST" }));
    } catch (err) {
      setError(passwordError((err as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.temporary_password);
      setCopied(true);
    } catch {
      /* clipboard blocked: the password is on screen to read out */
    }
  };

  const where = user.role === "ADMIN" ? "trang Đặt lại mật khẩu của web admin" : "mục Quên mật khẩu trên ứng dụng";

  return (
    <Dialog title="Cấp mật khẩu tạm" subtitle={`${user.name} · ${user.email}`} icon={KeyRound} onClose={onClose}>
      {issued ? (
        <>
          <Field label="Mật khẩu tạm" hint={`Hết hạn lúc ${fDate(issued.expires_at)} · chỉ hiện một lần`}>
            <div className="row">
              <input className="input mono" readOnly value={issued.temporary_password} style={{ fontSize: 16 }} />
              <button type="button" className="btn btn-outline" onClick={copy}>
                {copied ? <Check size={15} /> : <Copy size={15} />}
                {copied ? "Đã chép" : "Chép"}
              </button>
            </div>
          </Field>
          <p className="muted" style={{ fontSize: 13, margin: "4px 0 0" }}>
            Gửi mật khẩu này trực tiếp cho {user.name}. Họ vào {where}, nhập email, mật khẩu tạm và mật khẩu mới.
            Mật khẩu hiện tại của họ vẫn dùng được cho tới lúc đó.
          </p>
          <div className="df" style={{ padding: "16px 0 0" }}>
            <button className="btn btn-primary" onClick={onClose}>
              Xong
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Dùng khi người dùng quên mật khẩu. Hệ thống tạo một mật khẩu tạm có hiệu lực 60 phút; người dùng dùng nó
            như mật khẩu cũ để đặt mật khẩu mới. Cấp lại sẽ huỷ mật khẩu tạm trước đó.
          </p>
          {error && <div className="err">{error}</div>}
          <div className="df" style={{ padding: "8px 0 0" }}>
            <button type="button" className="btn btn-outline" onClick={onClose}>
              Huỷ
            </button>
            <button className="btn btn-primary" onClick={issue} disabled={busy}>
              {busy ? "Đang tạo…" : "Tạo mật khẩu tạm"}
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}
