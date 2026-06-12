import { api } from "./client";
import type { SettingsSnapshot } from "../types/api";

export const settingsApi = {
  get: () => api.get<SettingsSnapshot>("/settings").then((r) => r.data),
};
