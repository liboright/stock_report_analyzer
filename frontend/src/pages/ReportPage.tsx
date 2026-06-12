import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import CompanyYearSelector from "../components/CompanyYearSelector";
import { useCompanyYear } from "../hooks/useCompanyYear";
import { reportsApi } from "../api/reports";
import type { ReportContent, ReportRun } from "../types/api";

const statusColor: Record<string, string> = {
  pending: "default",
  parsing: "processing",
  done: "success",
  failed: "error",
  queued: "default",
  running: "processing",
  stage0: "processing",
  stage1: "processing",
  stage2: "processing",
  stage3: "processing",
  stage4: "processing",
};

export default function ReportPage() {
  const { company } = useCompanyYear();
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["reports", company],
    queryFn: () => reportsApi.listByCompany(company!),
    enabled: !!company,
  });

  const [openId, setOpenId] = useState<number | null>(null);
  const [content, setContent] = useState<ReportContent | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  const open = async (id: number) => {
    setOpenId(id);
    setContent(null);
    setLoadingContent(true);
    try {
      const c = await reportsApi.getContent(id);
      setContent(c);
    } catch {
      setContent(null);
    } finally {
      setLoadingContent(false);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <CompanyYearSelector />

      <Card
        title="历史报告运行"
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refetch()}
            disabled={!company}
          >
            刷新
          </Button>
        }
      >
        {!company ? (
          <Alert type="info" showIcon message="请先选择公司" />
        ) : isLoading ? (
          <Spin />
        ) : !data || data.length === 0 ? (
          <Empty description="还没有报告运行，先去「分析」页生成一个" />
        ) : (
          <Table<ReportRun>
            rowKey="id"
            dataSource={data}
            pagination={false}
            size="small"
            columns={[
              { title: "Run ID", dataIndex: "id", width: 80 },
              { title: "年份", dataIndex: "year", width: 80, render: (v) => v ?? "—" },
              { title: "Skill", dataIndex: "template", width: 220, ellipsis: true },
              {
                title: "状态",
                dataIndex: "status",
                width: 110,
                render: (s: string) => (
                  <Tag color={statusColor[s] ?? "default"}>{s}</Tag>
                ),
              },
              { title: "Stage", dataIndex: "current_stage", width: 70 },
              {
                title: "开始",
                dataIndex: "started_at",
                width: 170,
                render: (v) =>
                  v ? new Date(v).toLocaleString("zh-CN") : "—",
              },
              {
                title: "完成",
                dataIndex: "finished_at",
                width: 170,
                render: (v) =>
                  v ? new Date(v).toLocaleString("zh-CN") : "—",
              },
              { title: "错误", dataIndex: "error", ellipsis: true, render: (v) => v ?? "—" },
              {
                title: "操作",
                width: 90,
                render: (_, r) => (
                  <Button
                    size="small"
                    disabled={r.status !== "done"}
                    onClick={() => open(r.id)}
                  >
                    查看
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Modal
        title={openId ? `报告 Run #${openId}` : ""}
        open={openId !== null}
        onCancel={() => setOpenId(null)}
        footer={null}
        width={900}
        destroyOnClose
      >
        {loadingContent ? (
          <Spin />
        ) : content ? (
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 600,
              overflow: "auto",
              background: "#fafafa",
              padding: 12,
              borderRadius: 4,
              margin: 0,
            }}
          >
            {content.content}
          </pre>
        ) : (
          <Typography.Text type="secondary">无内容</Typography.Text>
        )}
      </Modal>
    </Space>
  );
}
