import { create } from "zustand";

export const useSession = create((set) => ({
  me: null,
  labId: typeof localStorage !== "undefined" ? localStorage.getItem("labId") || "" : "",
  unread: 0,
  unreadKnown: false,
  unreadErr: "",
  setMe: (me) => set({ me }),
  setLabId: (labId) => {
    if (labId) localStorage.setItem("labId", labId);
    set({ labId: labId || "" });
  },
  setUnread: (unread) => set({ unread: unread || 0, unreadKnown: true, unreadErr: "" }),
  setUnreadErr: (msg) => set({ unreadKnown: false, unreadErr: msg || "未读数加载失败" }),
  logout: () => {
    localStorage.removeItem("access");
    localStorage.removeItem("refresh");
    localStorage.removeItem("labId");
    set({ me: null, labId: "", unread: 0, unreadKnown: false, unreadErr: "" });
  },
}));
