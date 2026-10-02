import { ArrowRight, CheckCircle, CreditCard, Nfc, RefreshCw, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../shared/api";
import { useInterval, useNow } from "../../shared/hooks";
import { useRealtime } from "../../shared/realtime";
import { Dialog, Field, useToast } from "../../shared/ui";

/** apps/api/app/modules/admin/card_registration.py */
interface Registration {
  registration_id: string;
  reader_id: string;
  user_id: string;
  user_name: string | null;
  mode: "ASSIGN" | "REPLACE";
  replace_card_id: string | null;
  replace_uid: string | null;
  status: "WAITING" | "SCANNED" | "COMPLETED" | "CANCELLED" | "EXPIRED";
  uid: string | null;
  last_error: string | null;
  card_id: string | null;
  expires_at: string;
}

interface Reader {
  reader_id: string;
  is_online: boolean;
}

const OPEN = ["WAITING", "SCANNED"];

/** The reader LCD has no accents: "Phạm Minh Khoa" -> "PHAM MINH KHOA". */
const lcdText = (text: string) =>
  text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toUpperCase();

/**
 * Assign a new RFID card to a member (or replace one) by tapping it on the
 * admin desk reader. The UID arrives over /ws, with polling as a fallback.
 */
export function RegisterCardDialog({
  userId,
  userName,
  replaceCard,
  onClose,
  onDone,
}: {
  userId: string;
  userName: string;
  /** Set to swap this card for a new one ("Đổi thẻ"). */
  replaceCard?: { card_id: string; uid: string | null } | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const { subscribe } = useRealtime();
  const now = useNow(1000);
  const [registration, setRegistration] = useState<Registration | null>(null);
  const [reader, setReader] = useState<Reader | null>(null);
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const current = useRef<Registration | null>(null);
  current.current = registration;
  const started = useRef(false);
  const replacing = Boolean(replaceCard);

  const start = useCallback(async () => {
    setError(null);
    setLabel("");
    try {
      const reg = await api<Registration>("/admin/card-registrations", {
        method: "POST",
        body: replaceCard
          ? { user_id: userId, mode: "REPLACE", replace_card_id: replaceCard.card_id }
          : { user_id: userId, mode: "ASSIGN" },
      });
      setRegistration(reg);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [userId, replaceCard]);

  // Ask the reader for a card as soon as the dialog opens (once, even in StrictMode).
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void start();
    api<Reader>("/admin/card-registrations/reader").then(setReader).catch(() => {});
  }, [start]);

  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === "rfid_reader_status") setReader(event as unknown as Reader);
        const reg = current.current;
        if (event.type === "card_registration_updated" && reg && event.registration_id === reg.registration_id) {
          setRegistration(event as unknown as Registration);
        }
      }),
    [subscribe],
  );

  const poll = useCallback(async () => {
    const reg = current.current;
    if (!reg || !OPEN.includes(reg.status)) return;
    try {
      setRegistration(await api<Registration>(`/admin/card-registrations/${reg.registration_id}`));
    } catch {
      /* keep the last state */
    }
  }, []);
  useInterval(poll, registration && OPEN.includes(registration.status) ? 2000 : null);
  useInterval(() => {
    api<Reader>("/admin/card-registrations/reader").then(setReader).catch(() => {});
  }, 10_000);

  const open = registration !== null && OPEN.includes(registration.status);
  const secondsLeft = registration ? Math.max(0, Math.round((new Date(registration.expires_at).getTime() - now) / 1000)) : 0;
  useEffect(() => {
    if (open && secondsLeft === 0) void poll();
  }, [open, secondsLeft, poll]);

  const close = async () => {
    if (registration?.status === "COMPLETED") return onDone();
    if (open) {
      await api(`/admin/card-registrations/${registration!.registration_id}/cancel`, { method: "POST" }).catch(() => {});
    }
    onClose();
  };

  const complete = async () => {
    setBusy(true);
    setError(null);
    try {
      const done = await api<Registration>(`/admin/card-registrations/${registration!.registration_id}/complete`, {
        method: "POST",
        body: { label: label.trim() || null },
      });
      setRegistration(done);
      toast(replacing ? "Đã đổi thẻ" : "Đã gán thẻ", `${done.uid} → ${userName}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const status = registration?.status;
  const readerOffline = reader !== null && !reader.is_online;

  return (
    <Dialog
      title={replacing ? "Đổi thẻ RFID" : "Gán thẻ RFID"}
      subtitle={
        replacing
          ? `Thẻ mới sẽ thay thẻ ${replaceCard?.uid ?? ""} của ${userName}`
          : `Thẻ sẽ thuộc về ${userName} và dùng để bắt đầu sạc tại trạm`
      }
      icon={CreditCard}
      onClose={close}
      footer={
        status === "SCANNED" ? (
          <>
            <button className="btn btn-outline" onClick={close}>
              Huỷ
            </button>
            <button className="btn btn-primary" onClick={complete} disabled={busy}>
              <CheckCircle size={15} />
              Hoàn tất
            </button>
          </>
        ) : status === "COMPLETED" ? (
          <button className="btn btn-primary" onClick={close}>
            Xong
          </button>
        ) : status === "EXPIRED" || status === "CANCELLED" || (!registration && error) ? (
          <>
            <button className="btn btn-outline" onClick={onClose}>
              Đóng
            </button>
            <button className="btn btn-primary" onClick={start}>
              <RefreshCw size={15} />
              Quét lại
            </button>
          </>
        ) : (
          <button className="btn btn-outline" onClick={close}>
            Huỷ
          </button>
        )
      }
    >
      {(!registration || status === "WAITING") && !(!registration && error) && (
        <div style={{ textAlign: "center" }}>
          <div className="scan-anim">
            <Nfc size={34} />
          </div>
          <b>Quẹt thẻ {replacing ? "mới " : ""}lên đầu đọc tại quầy</b>
          <p className="muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
            Đầu đọc <span className="mono">{registration?.reader_id ?? "…"}</span> hiển thị{" "}
            <span className="mono">
              {replacing ? "DOI THE CHO" : "QUET THE CHO"}: {lcdText(userName)}
            </span>
          </p>
          {registration && (
            <p className="muted num" style={{ fontSize: 12.5 }}>
              Còn {secondsLeft} giây
            </p>
          )}
          {readerOffline && (
            <div className="m-note" style={{ textAlign: "left", marginTop: 8 }}>
              <WifiOff size={15} />
              <span>
                Đầu đọc đang offline. Mở simulator Wokwi (<span className="mono">simulator/admin</span>) hoặc cắm đầu đọc,
                yêu cầu quét vẫn chờ.
              </span>
            </div>
          )}
          {registration?.last_error && (
            <div className="err" style={{ marginTop: 8 }}>
              {registration.last_error}
            </div>
          )}
        </div>
      )}

      {status === "SCANNED" && registration && (
        <>
          {replacing && (
            <div className="row" style={{ justifyContent: "center", gap: 12, marginBottom: 12 }}>
              <span className="mono muted" style={{ textDecoration: "line-through" }}>
                {registration.replace_uid ?? replaceCard?.uid}
              </span>
              <ArrowRight size={16} />
              <span className="mono strong">{registration.uid}</span>
            </div>
          )}
          <div className="rfid" style={{ marginBottom: 14 }}>
            <div className="between">
              <span style={{ color: "var(--primary)" }}>
                <Nfc size={18} />
              </span>
              <span className="badge b-ready">Đã đọc thẻ</span>
            </div>
            <div className="mono" style={{ fontSize: 22, fontWeight: 600, margin: "16px 0 4px", letterSpacing: ".08em" }}>
              {registration.uid}
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              UID thẻ · quẹt thẻ khác để đổi UID trước khi hoàn tất
            </div>
          </div>
          <Field label="Nhãn thẻ (không bắt buộc)">
            <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="VD: Thẻ chính" autoFocus />
          </Field>
          {replacing && (
            <div className="hint">Thẻ cũ sẽ bị gỡ khỏi member và không dùng để sạc được nữa.</div>
          )}
        </>
      )}

      {status === "COMPLETED" && registration && (
        <div style={{ textAlign: "center" }}>
          <div className="scan-anim" style={{ animation: "none" }}>
            <CheckCircle size={34} />
          </div>
          <b>
            {replacing ? "Đã đổi sang thẻ" : "Đã gán thẻ"} {registration.uid}
          </b>
          <p className="muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
            {userName} có thể quẹt thẻ này tại trạm để bắt đầu sạc.
          </p>
        </div>
      )}

      {(status === "EXPIRED" || status === "CANCELLED") && (
        <div className="empty" style={{ padding: 20 }}>
          {status === "EXPIRED" ? "Hết thời gian chờ quẹt thẻ." : "Yêu cầu quét đã bị huỷ."}
        </div>
      )}

      {error && (
        <div className="err" style={{ marginTop: 8 }}>
          {error}
        </div>
      )}
    </Dialog>
  );
}
