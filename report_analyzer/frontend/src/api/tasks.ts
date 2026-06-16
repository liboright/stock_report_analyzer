import { api } from "./client";
import type { TaskSnapshot } from "../types/api";

export const tasksApi = {
  get: (runId: number) => api.get<TaskSnapshot>(`/tasks/${runId}`).then((r) => r.data),
};
