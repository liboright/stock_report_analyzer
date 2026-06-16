import { Button, Collapse, Empty, List, Space, Spin, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useFiles } from "../../hooks/useFiles";
import type { TableCsvFile } from "../../api/files";
import PreviewDrawer, {
  type PreviewTarget,
} from "../FilePreview";
import { useState } from "react";

/**
 * 解析阶段单 (公司, 年份) 的文件树面板（3 类产物：章节 / 第三节 H2 / 抽取表格）。
 * 这三类都是**分年**产物——每个年报目录独立。点击任一项会打开 PreviewDrawer 预览。
 *
 * 拆成独立组件的目的是让多个 Tab/多个父级各自持有 useFiles 状态，遵循
 * React Query 的 hooks 必须在顶层调用的规则。
 *
 * 范围对照：
 * - 解析阶段（按年）→ ParseYearFileTree（本组件）
 * - 分析阶段（公司级）→ AnalysisFileTree
 */
export default function ParseYearFileTree({
  company,
  year,
}: {
  company: string;
  year: number;
}) {
  const { data: tree, isLoading, refetch } = useFiles(company, year);
  const [preview, setPreview] = useState<PreviewTarget | null>(null);

  const tablesByCategory = (tree?.tables ?? []).reduce<
    Record<string, TableCsvFile[]>
  >((acc, t) => {
    (acc[t.category] ||= []).push(t);
    return acc;
  }, {});
  const orderedCategories = Object.keys(tablesByCategory).sort();

  if (isLoading) return <Spin />;
  const empty =
    !tree ||
    (tree.chapters.length === 0 &&
      tree.section3.length === 0 &&
      tree.tables.length === 0);

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <Space>
        <Button
          icon={<ReloadOutlined />}
          size="small"
          onClick={() => refetch()}
        >
          刷新 {year}
        </Button>
      </Space>
      {empty ? (
        <Empty description={`${year} 年尚未解析`} />
      ) : (
        <Collapse defaultActiveKey={["chapters"]}>
          <Collapse.Panel
            header={`章节 (${tree?.chapters.length ?? 0})`}
            key="chapters"
          >
            {tree && tree.chapters.length > 0 ? (
              <List
                size="small"
                dataSource={tree.chapters}
                renderItem={(c) => (
                  <List.Item>
                    <Space>
                      <Tag color="blue">{c.section_num || "??"}</Tag>
                      <a
                        onClick={() =>
                          setPreview({
                            path: c.path,
                            title: c.title,
                            kind: "md",
                          })
                        }
                      >
                        {c.title}
                      </a>
                    </Space>
                  </List.Item>
                )}
              />
            ) : (
              <Empty
                description="无章节文件"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </Collapse.Panel>
          <Collapse.Panel
            header={`第三节 H2 拆分 (${tree?.section3.length ?? 0})`}
            key="section3"
          >
            {tree && tree.section3.length > 0 ? (
              <List
                size="small"
                dataSource={tree.section3}
                renderItem={(s) => (
                  <List.Item>
                    <a
                      onClick={() =>
                        setPreview({
                          path: s.path,
                          title: s.title,
                          kind: "md",
                        })
                      }
                    >
                      {s.title}
                    </a>
                  </List.Item>
                )}
              />
            ) : (
              <Empty
                description="无 H2 拆分文件"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </Collapse.Panel>
          <Collapse.Panel
            header={`抽取表格 (${tree?.tables.length ?? 0})`}
            key="tables"
          >
            {tree && tree.tables.length > 0 ? (
              <Collapse size="small" ghost>
                {orderedCategories.map((cat) => (
                  <Collapse.Panel
                    header={
                      <Space>
                        <Tag color="geekblue">{cat}</Tag>
                        <Typography.Text type="secondary">
                          {tablesByCategory[cat].length}
                        </Typography.Text>
                      </Space>
                    }
                    key={cat}
                  >
                    <List
                      size="small"
                      dataSource={tablesByCategory[cat]}
                      renderItem={(t) => (
                        <List.Item>
                          <a
                            onClick={() =>
                              setPreview({
                                path: t.path,
                                title: `${t.category} / ${t.name}`,
                                kind: "raw",
                              })
                            }
                          >
                            {t.name}
                          </a>
                        </List.Item>
                      )}
                    />
                  </Collapse.Panel>
                ))}
              </Collapse>
            ) : (
              <Empty
                description="尚未抽取表格"
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
