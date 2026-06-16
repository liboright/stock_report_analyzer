import axios, { AxiosError } from "axios";
import { message } from "antd";

// vite.config.ts 把 /api/* 反代到 8000
export const api = axios.create({
  baseURL: "/api",
  timeout: 30_000,
});

// 响应拦截：后端 4xx/5xx 时弹 message，detail 优先
api.interceptors.response.use(
  (r) => r,
  (err: AxiosError<{ detail?: string }>) => {
    const detail = err.response?.data?.detail ?? err.message;
    // 409 是预期的业务状态（任务未完成），不弹错误
    if (err.response?.status !== 409) {
      message.error(`[${err.response?.status ?? "ERR"}] ${detail}`);
    }
    return Promise.reject(err);
  },
);
