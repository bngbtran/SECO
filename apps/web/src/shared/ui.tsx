import { HelpCircle, Lock, LockOpen, X, Zap, type LucideIcon } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import {
  AVAILABILITY_BADGE,
  AVAILABILITY_LABEL,
  PAYMENT_BADGE,
  ROLE_BADGE,
  SESSION_BADGE,
  SESSION_LABEL,
  SEVERITY_BADGE,
} from "./labels";

// --- Badges -------------------------------------------------------------------

export function Badge({ cls, children, dot = true }: { cls: string; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`badge ${cls}`}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export const AvailabilityBadge = ({ value }: { value: string }) => (
  <Badge cls={AVAILABILITY_BADGE[value] ?? "b-gray"}>{AVAILABILITY_LABEL[value] ?? value}</Badge>
);

export const SessionBadge = ({ value }: { value: string }) => (
  <Badge cls={SESSION_BADGE[value] ?? "b-gray"}>{SESSION_LABEL[value] ?? value}</Badge>
);

export const PaymentBadge = ({ value }: { value: string }) => (
  <Badge cls={PAYMENT_BADGE[value] ?? "b-gray"} dot={false}>
    {value}
  </Badge>
);

export const RoleBadge = ({ value }: { value: string }) => (
  <Badge cls={ROLE_BADGE[value] ?? "b-outline"} dot={false}>
    {value}
  </Badge>
);

export const SeverityBadge = ({ value }: { value: string }) => (
  <Badge cls={SEVERITY_BADGE[value] ?? "b-gray"} dot={false}>
    {value}
  </Badge>
);

export const TypeBadge = ({ value }: { value: string }) => (
  <Badge cls={value === "GUEST" ? "b-gray" : "b-outline"} dot={false}>
    {value}
  </Badge>
);

export function LockBadge({ value }: { value: string }) {
  if (value === "LOCKED")
    return (
      <span className="badge b-maint">
        <Lock size={12} />
        LOCKED
      </span>
    );
  if (value === "UNKNOWN")
    return (
      <span className="badge b-gray">
        <HelpCircle size={12} />
        UNKNOWN
      </span>
    );
  return (
    <span className="badge b-outline">
      <LockOpen size={12} />
      UNLOCKED
    </span>
  );
}

// --- Layout pieces --------------------------------------------------------------

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="ph">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  action,
  children,
  bodyStyle,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  bodyStyle?: React.CSSProperties;
  className?: string;
}) {
  return (
    <div className={`card ${className}`}>
      {(title || action) && (
        <div className="card-h">
          <div>
            {title && <h3>{title}</h3>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="card-b" style={bodyStyle}>
        {children}
      </div>
    </div>
  );
}

export function Kpi({
  label,
  value,
  unit,
  hint,
  icon: Icon,
  iconColor,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  icon?: LucideIcon;
  iconColor?: string;
}) {
  return (
    <div className="card kpi">
      <div className="k-top">
        {label}
        {Icon && (
          <span className="k-ic" style={iconColor ? { color: iconColor } : undefined}>
            <Icon size={16} />
          </span>
        )}
      </div>
      <div className="k-v">
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {hint && <div className="k-d">{hint}</div>}
    </div>
  );
}

export function Empty({ icon: Icon = Zap, children }: { icon?: LucideIcon; children: ReactNode }) {
  return (
    <div className="empty">
      <div className="ei">
        <Icon size={20} />
      </div>
      {children}
    </div>
  );
}

export function Loading() {
  return <div className="empty">Đang tải…</div>;
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="empty">
      <div className="err" style={{ marginBottom: 10 }}>
        {message}
      </div>
      {onRetry && (
        <button className="btn btn-outline btn-sm" onClick={onRetry}>
          Thử lại
        </button>
      )}
    </div>
  );
}

export function Pager({
  total,
  limit,
  offset,
  onChange,
  summary,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
  summary?: ReactNode;
}) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="pager">
      <span>{summary ?? `${total} bản ghi`}</span>
      <div className="row">
        <span>
          Trang {page}/{pages}
        </span>
        <button className="btn btn-outline btn-sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Trước
        </button>
        <button className="btn btn-outline btn-sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Sau
        </button>
      </div>
    </div>
  );
}

export function Chip({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button className={`chip ${on ? "on" : ""}`} onClick={onClick}>
      {children}
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
}) {
  return (
    <div className="seg">
      {options.map(([key, label]) => (
        <button key={key} className={value === key ? "on" : ""} onClick={() => onChange(key)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <label className="label">{label}</label>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

// --- Dialog ---------------------------------------------------------------------

export function Dialog({
  title,
  subtitle,
  icon: Icon,
  onClose,
  children,
  footer,
}: {
  title: string;
  subtitle?: ReactNode;
  icon?: LucideIcon;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <div className="dh">
          <div className="between">
            <div className="row" style={{ gap: 10 }}>
              {Icon && (
                <span className="brand-mark" style={{ background: "var(--primary-50)", color: "var(--primary)" }}>
                  <Icon size={18} />
                </span>
              )}
              <div>
                <h3>{title}</h3>
                {subtitle && <p>{subtitle}</p>}
              </div>
            </div>
            <button className="btn btn-ghost btn-sm btn-icon" onClick={onClose} aria-label="Đóng">
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="db">{children}</div>
        {footer && <div className="df">{footer}</div>}
      </div>
    </div>
  );
}

// --- Toasts ---------------------------------------------------------------------

interface Toast {
  id: number;
  title: string;
  message?: string;
  tone: "ok" | "error";
}

const ToastContext = createContext<(title: string, message?: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((title: string, message?: string, tone: Toast["tone"] = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, title, message, tone }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4200);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast" key={t.id}>
            <span className="ti" style={t.tone === "error" ? { color: "var(--red)" } : undefined}>
              <Zap size={16} />
            </span>
            <div>
              <b>{t.title}</b>
              {t.message && <span>{t.message}</span>}
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
