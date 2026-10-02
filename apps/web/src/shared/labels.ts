export const AVAILABILITY_LABEL: Record<string, string> = {
  AVAILABLE: "Sẵn sàng",
  CHARGING: "Đang sạc",
  WAITING_PAYMENT: "Chờ thanh toán",
  ERROR: "Lỗi",
  MAINTENANCE: "Bảo trì",
  OFFLINE: "Offline",
};

export const AVAILABILITY_BADGE: Record<string, string> = {
  AVAILABLE: "b-ready",
  CHARGING: "b-charging",
  WAITING_PAYMENT: "b-maint",
  ERROR: "b-error",
  MAINTENANCE: "b-maint",
  OFFLINE: "b-gray",
};

export const AVAILABILITY_COLOR: Record<string, string> = {
  AVAILABLE: "#437066",
  CHARGING: "#2e6fb7",
  WAITING_PAYMENT: "#d69e2e",
  ERROR: "#c8423f",
  MAINTENANCE: "#b7791f",
  OFFLINE: "#8a9793",
};

export const SESSION_LABEL: Record<string, string> = {
  ACTIVE: "Đang sạc",
  WAITING_PAYMENT: "Chờ thanh toán",
  COMPLETED: "Hoàn tất",
  ERROR: "Lỗi",
};

export const SESSION_BADGE: Record<string, string> = {
  ACTIVE: "b-charging",
  WAITING_PAYMENT: "b-maint",
  COMPLETED: "b-ready",
  ERROR: "b-error",
};

export const PAYMENT_BADGE: Record<string, string> = {
  PENDING: "b-maint",
  PAID: "b-ready",
  EXPIRED: "b-gray",
  CANCELLED: "b-error",
};

export const STOP_LABEL: Record<string, string> = {
  UNPLUG: "Rút sạc",
  MAX_BUDGET: "Đạt ngân sách",
  INSUFFICIENT_BALANCE: "Không đủ số dư",
  TECHNICIAN: "Kỹ thuật viên / Admin",
  OVER_TEMPERATURE: "Quá nhiệt",
  E_STOP: "E-Stop",
  SYSTEM_ERROR: "Lỗi hệ thống",
};

export const TX_LABEL: Record<string, string> = {
  CHARGING: "Trừ tiền phiên sạc",
  GUEST_PAYMENT: "Thanh toán Guest",
  TOPUP: "Nạp tiền (Demo)",
};

export const ERROR_LABEL: Record<string, string> = {
  OVER_TEMPERATURE: "Quá nhiệt",
  E_STOP: "E-Stop",
  RELAY_FAULT: "Lỗi relay",
  COMMUNICATION_ERROR: "Mất kết nối",
  SENSOR_ERROR: "Lỗi cảm biến",
  SYSTEM_ERROR: "Lỗi hệ thống",
};

export const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: "b-error",
  ERROR: "b-maint",
  WARNING: "b-gray",
};

export const ROLE_BADGE: Record<string, string> = {
  USER: "b-outline",
  ADMIN: "b-ready",
  TECHNICIAN: "b-maint",
};

export const ROLE_LABEL: Record<string, string> = {
  USER: "Member",
  ADMIN: "Admin",
  TECHNICIAN: "Kỹ thuật viên",
};

export const CARD_BADGE: Record<string, string> = {
  ACTIVE: "b-ready",
  PENDING: "b-maint",
  INACTIVE: "b-gray",
  UNASSIGNED: "b-outline",
};
