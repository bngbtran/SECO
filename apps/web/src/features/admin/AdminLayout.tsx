import {
  AlertTriangle,
  BatteryCharging,
  Bell,
  ChevronRight,
  Home,
  LayoutDashboard,
  LogOut,
  Receipt,
  Users,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../../shared/auth";
import { initials } from "../../shared/format";
import { useApi } from "../../shared/hooks";
import { useRealtime, useRealtimeRefresh } from "../../shared/realtime";
import type { AdminErrorLog, Page } from "../../shared/types";

const NAV: [string, [string, LucideIcon, string][]][] = [
  ["Tổng quan", [["/", LayoutDashboard, "Dashboard"]]],
  [
    "Vận hành",
    [
      ["/stations", Zap, "Trạm sạc"],
      ["/sessions", BatteryCharging, "Phiên sạc"],
      ["/faults", AlertTriangle, "Sự cố"],
      ["/maintenance", Wrench, "Bảo trì"],
    ],
  ],
  ["Người dùng", [["/users", Users, "Người dùng"]]],
  ["Tài chính", [["/transactions", Receipt, "Giao dịch & thanh toán"]]],
];

const TITLES: Record<string, [string, string?]> = {
  "": ["Dashboard"],
  stations: ["Trạm sạc", "Chi tiết trạm"],
  sessions: ["Phiên sạc", "Chi tiết phiên"],
  users: ["Người dùng", "Chi tiết người dùng"],
  transactions: ["Giao dịch & thanh toán"],
  faults: ["Sự cố"],
  maintenance: ["Bảo trì"],
};

function Breadcrumb() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [section, id] = pathname.split("/").filter(Boolean);
  const [listTitle, detailTitle] = TITLES[section ?? ""] ?? ["Dashboard"];

  return (
    <div className="crumb">
      <Home size={14} />
      <span>Admin</span>
      <ChevronRight size={14} />
      {id ? (
        <>
          <span style={{ cursor: "pointer" }} onClick={() => navigate(`/${section}`)}>
            {listTitle}
          </span>
          <ChevronRight size={14} />
          <b>{detailTitle}</b>
        </>
      ) : (
        <b>{listTitle}</b>
      )}
    </div>
  );
}

export default function AdminLayout() {
  const { user, logout } = useAuth();
  const { connected } = useRealtime();
  const navigate = useNavigate();
  const openErrors = useApi<Page<AdminErrorLog>>("/admin/errors", { resolved: false, limit: 1 });
  useRealtimeRefresh(openErrors.reload, (e) => e.type === "station_error" || e.type === "station_updated");
  const openCount = openErrors.data?.total ?? 0;

  return (
    <div className="admin">
      <aside className="side">
        <div className="brand">
          <span className="brand-mark">
            <Zap size={18} />
          </span>
          <div>
            SECO<small>Web Admin</small>
          </div>
        </div>
        {NAV.map(([group, items]) => (
          <div key={group}>
            <div className="grp">{group}</div>
            {items.map(([to, Icon, label]) => (
              <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `nav ${isActive ? "on" : ""}`}>
                <Icon size={16} />
                {label}
                {to === "/faults" && openCount > 0 && <span className="cnt">{openCount}</span>}
              </NavLink>
            ))}
          </div>
        ))}
        <div className="side-foot">
          <div className="row">
            <div className="avatar">{initials(user?.name ?? "A")}</div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="strong" style={{ fontSize: 13 }}>
                {user?.name}
              </div>
              <div className="muted" style={{ fontSize: 11.5, overflow: "hidden", textOverflow: "ellipsis" }}>
                {user?.role} · {user?.email}
              </div>
            </div>
          </div>
          <button
            className="btn btn-ghost btn-sm w-full logout"
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
          >
            <LogOut size={15} />
            Đăng xuất
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="ahead">
          <Breadcrumb />
          <div style={{ flex: 1 }} />
          <span className="pill-live" title={connected ? "Đang nhận dữ liệu realtime" : "Mất kết nối, đang thử lại"}>
            <span className={`dot ${connected ? "pulse" : "off"}`} />
            MQTT · WebSocket
          </span>
          <button className="btn btn-outline btn-icon bell" title="Sự cố" onClick={() => navigate("/faults")}>
            <Bell size={16} />
            {openCount > 0 && <span className="cnt">{openCount}</span>}
          </button>
        </header>
        <div className="acontent">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
