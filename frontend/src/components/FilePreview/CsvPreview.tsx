import { useMemo } from "react";
import { Alert, Empty, Table, Typography } from "antd";
import Papa from "papaparse";

interface Props {
  content: string;
}

interface CsvColumn {
  title: string;
  dataIndex: string;
  key: string;
  align: "left" | "right";
  ellipsis: boolean;
  render: (v: string) => React.ReactNode;
}

interface ParseResult {
  columns: CsvColumn[];
  dataSource: Record<string, string>[];
  errors: string[];
}

// 数字/含千分位/百分号/货币字符 → 右对齐
const isNumericLike = (s: string): boolean =>
  /^[\d,.%\-+¥$ \u00A0]+$/.test(s.trim()) && /[\d]/.test(s);

// 列对齐推断：扫前 200 行非空 cell，按多数决定
const detectColumnAlign = (rows: string[][], colIdx: number): "left" | "right" => {
  const sample: string[] = [];
  for (let i = 0; i < Math.min(rows.length, 200); i++) {
    const v = (rows[i][colIdx] ?? "").trim();
    if (v) sample.push(v);
  }
  if (sample.length === 0) return "left";
  const numeric = sample.filter(isNumericLike).length;
  return numeric / sample.length >= 0.6 ? "right" : "left";
};

const truncate = (s: string, n = 80) =>
  s.length > n ? s.slice(0, n) + "…" : s;

export default function CsvPreview({ content }: Props) {
  const parsed = useMemo<ParseResult>(() => {
    const result = Papa.parse<string[]>(content, {
      header: false,
      skipEmptyLines: true,
      dynamicTyping: false,
    });

    const rows: string[][] = (result.data ?? []).filter(
      (r) => Array.isArray(r) && r.length > 0,
    );

    if (rows.length === 0) {
      return { columns: [], dataSource: [], errors: [] };
    }

    // 第一行当表头
    const headerRow = rows[0];
    const bodyRows = rows.slice(1);

    // 补齐列数（避免 antd Table dataIndex 缺失）
    const colCount = headerRow.length;
    const paddedBody = bodyRows.map((r) => {
      if (r.length === colCount) return r;
      const out = r.slice(0, colCount);
      while (out.length < colCount) out.push("");
      return out;
    });

    const columns: CsvColumn[] = headerRow.map((rawTitle, idx) => {
      const title = (rawTitle ?? "").toString().trim() || `列${idx + 1}`;
      const align = detectColumnAlign(paddedBody, idx);
      return {
        title,
        dataIndex: `c${idx}`,
        key: `c${idx}`,
        align,
        ellipsis: true,
        render: (v: string) => {
          if (v == null || v === "") {
            return <Typography.Text type="secondary">—</Typography.Text>;
          }
          const display = truncate(v, 200);
          return v.length > 200 ? (
            <span title={v}>{display}</span>
          ) : (
            display
          );
        },
      };
    });

    const dataSource = paddedBody.map((r, i) => {
      const row: Record<string, string> = { key: String(i) };
      for (let j = 0; j < colCount; j++) {
        row[`c${j}`] = r[j] ?? "";
      }
      return row;
    });

    const errors = (result.errors ?? [])
      .slice(0, 3)
      .map((e) => `[row ${e.row ?? "?"}] ${e.message}`);

    return { columns, dataSource, errors };
  }, [content]);

  if (parsed.columns.length === 0) {
    return <Empty description="无数据" />;
  }

  return (
    <>
      {parsed.errors.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`解析出现 ${parsed.errors.length} 条告警`}
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {parsed.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          }
        />
      )}
      <Table<Record<string, string>>
        size="small"
        bordered
        columns={parsed.columns}
        dataSource={parsed.dataSource}
        scroll={{ x: "max-content" }}
        pagination={{
          pageSize: 50,
          showSizeChanger: true,
          pageSizeOptions: [50, 100, 500],
          showTotal: (total) => `共 ${total} 行`,
        }}
      />
    </>
  );
}
