import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** 跨页面共享的公司+年份上下文：URL `?company&year&years`。
 *
 * - `year`：单选年份，用于「按 year 查看文件树/生成报告」等老语义（AnalysisPage/ReportPage/SearchUploadPage/ParsePage 文件树）
 * - `years`：多选年份，用于 ParsePage「一键解析」批量调度；为空时由调用方按需 fallback 到全公司年份
 */
export function useCompanyYear() {
  const [params, setParams] = useSearchParams();
  const company = params.get("company") ?? "";
  const yearParam = params.get("year");
  const yearsParam = params.get("years");
  const yearsFromUrl: number[] = yearsParam
    ? yearsParam
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((n) => Number.isFinite(n) && n > 0)
    : [];
  // 多选年份：有 years 就用它，否则 fallback 为 year（单选）或今年
  const years: number[] =
    yearsFromUrl.length > 0
      ? yearsFromUrl
      : yearParam
        ? [Number(yearParam)]
        : [];
  // 单选年份：years[0]（多选时）→ 选中年份的首个；无多选时 → URL year / 今年
  const year = years.length > 0 ? years[0] : new Date().getFullYear();

  const setCompany = useCallback(
    (c: string | undefined) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (c) next.set("company", c);
          else next.delete("company");
          // 切换公司时清掉旧 years（旧公司的年份对新公司无意义）
          next.delete("years");
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const setYear = useCallback(
    (y: number) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("year", String(y));
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const setYears = useCallback(
    (ys: number[]) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (ys.length === 0) {
            next.delete("years");
            // 不动 year（让 fallback 到 new Date().getFullYear()）
          } else {
            // 按降序去重存
            const uniq = Array.from(new Set(ys)).sort((a, b) => b - a);
            next.set("years", uniq.join(","));
            // 关键：同步 year = years[0]，避免旧 ?year= 残留导致 year/years 不一致
            // （否则 React Router 的 setSearchParams 在同一事件 handler 多次调用时
            //  会因为 prev 都是「调用前的 URL」而互相覆盖）
            next.set("year", String(uniq[0]));
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return { company, year, years, setCompany, setYear, setYears } as const;
}
