import { useState } from "react";
import {
  Button,
  Collapse,
  Empty,
  List,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useFiles } from "../../hooks/useFiles";
import type { MergedTableFile } from "../../api/files";
import PreviewDrawer, {
  type PreviewTarget,
} from "../FilePreview";

/**
 * 分析阶段公司级文件树面板（2 类产物：研究报告 / 合并表格）。
 *
 * 这两类都是**跨年 / 公司级**产物：
 * - 研究报告：`{公司}/md/research_file/{公司}_业务概况*.md` / `{公司}_行业分析.md`
 * - 合并表格：`{公司}/md/research_file/table/{NNN}_{表名}_long.csv` / `_wide.csv`
 *
 * 后端 `GET /companies/{name}/reports/{year}/files` 返回的 `research` /
 * `merged_tables` 字段**不依赖 year**（按 docs/artifacts.md §1，它们从
 * `research_file/` 读，不带年）。这里仍要求传一个 `year`（用最新年）以满足
 * `useFiles` 的 `enabled`；当公司还没年报记录时回退 `null`。
 *
 * 范围对照：
 * - 解析阶段（按年）→ ParseYearFileTree
 * - 分析阶段（公司级）→ AnalysisFileTree（本组件）
 */
export default function AnalysisFileTree({
  company,
  year,
}: {
  company: string;
  /** 仅用于触发 `useFiles`；不影响实际产物（公司级不分年）。 */
  year: number | null;
}) {
  const { data: tree, isLoading, refetch } = useFiles(
    company,
    year ?? undefined,
  );
  const [preview, setPreview] = useState<PreviewTarget | null>(null);

  if (isLoading) return <Spin />;
  const empty =
    !tree ||
    (tree.research.length === 0 &&
      (tree.merged_tables?.length ?? 0) === 0);

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <Space>
        <Button
          icon={<ReloadOutlined />}
          size="small"
          onClick={() => refetch()}
        >
          刷新
        </Button>
      </Space>
      {empty ? (
        <Empty description="尚未生成分析产物" />
      ) : (
        <Collapse defaultActiveKey={["research", "merged_tables"]}>
          <Collapse.Panel
            header={`研究报告 (${tree?.research.length ?? 0})`}
            key="research"
          >
            {tree && tree.research.length > 0 ? (
              <List
                size="small"
                dataSource={tree.research}
                renderItem={(r) => (
                  <List.Item>
                    <Space>
                      {r.kind === "business" && <Tag color="blue">业务</Tag>}
                      {r.kind === "industry" && <Tag color="purple">行业</Tag>}
                      <a
                        onClick={() =>
                          setPreview({
                            path: r.path,
                            title: r.title,
                            kind: "md",
                          })
                        }
                      >
                        {r.title}
                      </a>
                    </Space>
                  </List.Item>
                )}
              />
            ) : (
              <Empty
                description="尚未生成研究报告"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </Collapse.Panel>
          <Collapse.Panel
            header={`合并表格 (${tree?.merged_tables?.length ?? 0})`}
            key="merged_tables"
          >
            {tree && (tree.merged_tables?.length ?? 0) > 0 ? (
              <Collapse size="small" ghost>
                {(tree.merged_tables ?? []).map((g) => (
                  <Collapse.Panel
                    key={g.group_key}
                    header={
                      <Space size={6}>
                        <Typography.Text strong>
                          {g.group_key}
                        </Typography.Text>
                        <Typography.Text type="secondary">
                          {g.sanitized_title}
                        </Typography.Text>
                      </Space>
                    }
                  >
                    <MergedTableRow group={g} onPreview={setPreview} />
                  </Collapse.Panel>
                ))}
              </Collapse>
            ) : (
              <Empty
                description="尚未合并表格"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </Collapse.Panel>
        </Collapse>
      )}
      <PreviewDrawer value={preview} onClose={() => setPreview(null)} />
    </Space>
  );
}

/** 合并表格一行：long + wide 两个 CSV（缺一显示「未生成」）。 */
function MergedTableRow({
  group,
  onPreview,
}: {
  group: MergedTableFile;
  onPreview: (t: PreviewTarget) => void;
}) {
  return (
    <List
      size="small"
      dataSource={[
        { label: "长表 (_long.csv)", path: group.long_csv },
        { label: "宽表 (_wide.csv)", path: group.wide_csv },
      ]}
      renderItem={(item) => (
        <List.Item>
          <Space>
            <Typography.Text type="secondary" style={{ minWidth: 110 }}>
              {item.label}
            </Typography.Text>
            {item.path ? (
              <a
                onClick={() =>
                  onPreview({
                    path: item.path!,
                    title: `${group.group_key} ${item.label}`,
                    kind: "raw",
                  })
                }
              >
                {item.path.split("/").pop()}
              </a>
            ) : (
              <Typography.Text type="secondary">未生成</Typography.Text>
            )}
          </Space>
        </List.Item>
      )}
    />
  );
}
