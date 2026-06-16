import { useEffect } from "react";
import { Card, Select, Space, Typography } from "antd";
import { useCompanies } from "../hooks/useCompanies";
import { useCompany } from "../hooks/useCompanies";
import { useCompanyYear } from "../hooks/useCompanyYear";

/** 4 个工作流页面顶部共享：公司 + 年份多选选择器。
 *
 * - 公司下拉：不变
 * - 年份多选：从公司详情的 annual_reports 中提取已存在的年份，
 *   默认全选。ParsePage 读取 `years` 做批量解析；其余页面
 *   仍旧读 `year`（= years[0]）保持向后兼容。
 */
export default function CompanyYearSelector() {
  const { data: companies } = useCompanies();
  const { company, years, setCompany, setYears } =
    useCompanyYear();

  const { data: companyDetail } = useCompany(company || undefined);
  const availableYears =
    companyDetail?.annual_reports
      ?.map((r) => r.year)
      .sort((a, b) => b - a) ?? [];

  // 选中值：若 years 已设定则用它，否则用 availableYears（全选）+ 页面刚加载时
  // 默认 year 可能和 availableYears 对不上，用 availableYears 初始化
  const effectiveYears =
    years.length > 0 ? years : availableYears.length > 0 ? availableYears : [];

  const handleYearsChange = (selected: number[]) => {
    if (selected.length === 0) return; // 至少选一个
    // setYears 内部已同步更新 year=years[0]，无需再单独 setYear
    setYears(selected);
  };

  // 当 availableYears 加载完成且 URL 中 ?years 尚未设定时，初始化全选。
  // 用 useEffect 避免 render 阶段触发 setState。
  useEffect(() => {
    if (!company || availableYears.length === 0) return;
    const urlYearsPresent =
      new URLSearchParams(window.location.search).get("years") !== null;
    if (urlYearsPresent) return;
    // 首次进入公司：全选所有可用年份（setYears 内部会同步 year=availableYears[0]）
    setYears(availableYears);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company, availableYears.join(",")]);

  return (
    <Card size="small" style={{ marginBottom: 16 }}>
      <Space size="middle" align="center" wrap>
        <Typography.Text strong>公司</Typography.Text>
        <Select
          showSearch
          allowClear
          placeholder="请选择公司"
          style={{ minWidth: 240 }}
          value={company || undefined}
          onChange={(v) => {
            setCompany(v);
            // 清 years 由 setCompany 自动处理
            // 可用年份由新的 availableYears 触发初始化
            // year 也被 URL 清掉，等 availableYears 加载后重置
          }}
          filterOption={(input, opt) =>
            (opt?.label as string ?? "").includes(input)
          }
          options={(companies ?? []).map((c) => ({
            value: c.name,
            label: c.stock_code ? `${c.name} (${c.stock_code})` : c.name,
          }))}
        />
        <Typography.Text strong>年份</Typography.Text>
        <Select
          mode="multiple"
          placeholder="选择要处理的年份"
          style={{ minWidth: 260 }}
          value={effectiveYears}
          onChange={handleYearsChange}
          options={availableYears.map((y) => ({
            value: y,
            label: String(y),
          }))}
          maxTagCount={4}
          tagRender={(props) => {
            const { label, closable, onClose } = props;
            return (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  margin: "2px 4px 2px 0",
                  padding: "0 6px",
                  fontSize: 13,
                  border: "1px solid #d9d9d9",
                  borderRadius: 4,
                  background: "#fafafa",
                  cursor: "default",
                }}
              >
                {label}
                {closable && (
                  <span
                    onClick={onClose}
                    style={{
                      marginLeft: 4,
                      cursor: "pointer",
                      color: "#999",
                      fontSize: 12,
                    }}
                  >
                    ×
                  </span>
                )}
              </span>
            );
          }}
        />
      </Space>
    </Card>
  );
}