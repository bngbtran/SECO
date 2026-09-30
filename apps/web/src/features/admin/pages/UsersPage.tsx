import { Search, UserPlus, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { fDay, initials, vnd } from "../../../shared/format";
import { useApi } from "../../../shared/hooks";
import { ROLE_LABEL } from "../../../shared/labels";
import type { AdminUser, Page } from "../../../shared/types";
import { Badge, Chip, Empty, ErrorBox, Loading, PageHeader, Pager, RoleBadge, Segmented } from "../../../shared/ui";
import { UserFormDialog } from "../components";

const PAGE_SIZE = 20;

export default function UsersPage() {
  const navigate = useNavigate();
  const [role, setRole] = useState("");
  const [active, setActive] = useState<"ALL" | "ACTIVE" | "LOCKED">("ALL");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);

  // Debounce typing before hitting the API.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setQ(search.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(id);
  }, [search]);

  const users = useApi<Page<AdminUser>>("/admin/users", {
    q,
    role,
    is_active: active === "ALL" ? undefined : active === "ACTIVE",
    limit: PAGE_SIZE,
    offset,
  });

  return (
    <>
      <PageHeader
        title="Người dùng"
        subtitle="Member, kỹ thuật viên và quản trị viên · số dư ví balance_vnd"
        actions={
          <button className="btn btn-primary" onClick={() => setAdding(true)}>
            <UserPlus size={15} />
            Tạo member
          </button>
        }
      />

      <div className="card">
        <div className="toolbar">
          <div className="input-wrap">
            <Search size={16} />
            <input className="input" placeholder="Tên, email, số điện thoại" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          {[
            ["", "Tất cả vai trò"],
            ["USER", "Member"],
            ["TECHNICIAN", "Kỹ thuật viên"],
            ["ADMIN", "Admin"],
          ].map(([k, label]) => (
            <Chip
              key={k}
              on={role === k}
              onClick={() => {
                setRole(k);
                setOffset(0);
              }}
            >
              {label}
            </Chip>
          ))}
          <div style={{ flex: 1 }} />
          <Segmented
            value={active}
            onChange={(v) => {
              setActive(v);
              setOffset(0);
            }}
            options={[
              ["ALL", "Tất cả"],
              ["ACTIVE", "Hoạt động"],
              ["LOCKED", "Đã khoá"],
            ]}
          />
        </div>

        {users.error ? (
          <ErrorBox message={users.error} onRetry={users.reload} />
        ) : !users.data ? (
          <Loading />
        ) : !users.data.items.length ? (
          <Empty icon={Users}>Không có người dùng phù hợp</Empty>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Người dùng</th>
                  <th>Vai trò</th>
                  <th>Điện thoại</th>
                  <th className="r">Thẻ RFID</th>
                  <th className="r">Số dư ví</th>
                  <th>Trạng thái</th>
                  <th>Ngày tạo</th>
                </tr>
              </thead>
              <tbody>
                {users.data.items.map((u) => (
                  <tr key={u.user_id} className="click" onClick={() => navigate(`/users/${u.user_id}`)}>
                    <td>
                      <div className="row">
                        <div className="avatar">{initials(u.name)}</div>
                        <div>
                          <div className="strong">{u.name}</div>
                          <div className="muted" style={{ fontSize: 12 }}>
                            {u.email}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <RoleBadge value={u.role} />
                      <span className="muted" style={{ fontSize: 12, marginLeft: 6 }}>
                        {ROLE_LABEL[u.role]}
                      </span>
                    </td>
                    <td className="muted">{u.phone ?? "—"}</td>
                    <td className="r num">{u.card_count || <span className="muted">—</span>}</td>
                    <td className="r num strong">{u.role === "USER" ? vnd(u.balance_vnd) : <span className="muted">—</span>}</td>
                    <td>{u.is_active ? <Badge cls="b-ready">Hoạt động</Badge> : <Badge cls="b-gray">Đã khoá</Badge>}</td>
                    <td className="muted">{fDay(u.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {users.data && (
          <Pager
            total={users.data.total}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            summary={`${users.data.total} người dùng`}
          />
        )}
      </div>

      {adding && (
        <UserFormDialog
          onClose={() => setAdding(false)}
          onSaved={(user) => {
            setAdding(false);
            // Next step of onboarding: tap the member's RFID card on the desk reader.
            navigate(`/users/${user.user_id}${user.role === "ADMIN" ? "" : "?assignCard=1"}`);
          }}
        />
      )}
    </>
  );
}
