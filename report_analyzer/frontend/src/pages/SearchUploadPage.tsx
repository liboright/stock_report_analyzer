import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import {
  CheckCircleTwoTone,
  CloseCircleTwoTone,
  DownloadOutlined,
  InboxOutlined,
  LoadingOutlined,
  PlusOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import CompanyYearSelector from "../components/CompanyYearSelector";
import { useCompanies, useCreateCompany } from "../hooks/useCompanies";
import { useCompanyYear } from "../hooks/useCompanyYear";
import { useTaskStream } from "../hooks/useTaskStream";
import { api } from "../api/client";
import { reportsApi } from "../api/reports";
import type { Company } from "../types/api";

export default function SearchUploadPage() {
  const { data: companies, isLoading } = useCompanies();
  const create = useCreateCompany();
  const qc = useQueryClient();
  const { company, year } = useCompanyYear();

  const [q, setQ] = useState("");
  const [newOpen, setNewOpen] = useState(false);
  const [newForm] = Form.useForm<{ name: string }>();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  // 年报在线下载
  const currentYear = new Date().getFullYear();
  const yearOptions = useMemo(
    () => [currentYear - 1, currentYear - 2, currentYear - 3, currentYear - 4, currentYear - 5],
    [currentYear],
  );
  const [downloadOpen, setDownloadOpen] = useState(false);
  const [downloadTarget, setDownloadTarget] = useState<Company | null>(null);
  const [selectedYears, setSelectedYears] = useState<number[]>([]);
  const [downloading, setDownloading] = useState(false);

  // 进度 Drawer
  const [progressOpen, setProgressOpen] = useState(false);
  const [runId, setRunId] = useState<number | null>(null);
  const taskStream = useTaskStream(progressOpen ? runId : null);

  const filtered = useMemo(() => {
    const list = companies ?? [];
    if (!q.trim()) return list;
    const k = q.trim().toLowerCase();
    return list.filter(
      (c) =>
        c.name.toLowerCase().includes(k) ||
        (c.stock_code ?? "").toLowerCase().includes(k),
    );
  }, [companies, q]);

  const onCreate = async () => {
    const v = await newForm.validateFields();
    try {
      await create.mutateAsync(v.name.trim());
      message.success(`已创建公司：${v.name}`);
      newForm.resetFields();
      setNewOpen(false);
    } catch {
      /* 拦截器已弹 */
    }
  };

  const onUpload = async () => {
    if (!file) {
      message.warning("请选择 PDF 文件");
      return;
    }
    if (!company) {
      message.warning("请先在顶部选择公司");
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("year", String(year));
    setUploading(true);
    try {
      await api.post(`/companies/${encodeURIComponent(company)}/upload`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      message.success(`${company} ${year} 年报上传成功`);
      setUploadOpen(false);
      setFile(null);
      qc.invalidateQueries({ queryKey: ["companies"] });
    } catch {
      /* 拦截器已弹 */
    } finally {
      setUploading(false);
    }
  };

  const openDownloadModal = (target: Company) => {
    setDownloadTarget(target);
    setSelectedYears([yearOptions[0], yearOptions[1], yearOptions[2]]); // 默认最近 3 年
    setDownloadOpen(true);
  };

  const onConfirmDownload = async () => {
    if (!downloadTarget) return;
    if (selectedYears.length === 0) {
      message.warning("请至少选择 1 个年份");
      return;
    }
    setDownloading(true);
    try {
      const res = await reportsApi.download({
        company: downloadTarget.name,
        years: selectedYears,
      });
      message.success(res.message);
      setDownloadOpen(false);
      setRunId(res.run_id);
      setProgressOpen(true);
    } catch {
      /* 拦截器已弹 */
    } finally {
      setDownloading(false);
    }
  };

  const closeProgress = () => {
    setProgressOpen(false);
    setRunId(null);
    // 刷新公司列表（年报状态从「无」变「有」）
    qc.invalidateQueries({ queryKey: ["companies"] });
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <CompanyYearSelector />

      <Row gutter={16}>
        <Col xs={24} md={12}>
          <Card
            title="搜索公司"
            extra={
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setNewOpen(true)}
              >
                新建公司
              </Button>
            }
          >
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="输入公司名 / 股票代码"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ marginBottom: 12 }}
            />
            {isLoading ? (
              <Spin />
            ) : filtered.length === 0 ? (
              <Empty description="未找到公司" />
            ) : (
              <Table<Company>
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={filtered}
                columns={[
                  { title: "公司名", dataIndex: "name" },
                  {
                    title: "代码",
                    dataIndex: "stock_code",
                    width: 100,
                    render: (v) => v ?? <Tag>未填</Tag>,
                  },
                  {
                    title: "下载",
                    width: 100,
                    render: (_, r) => {
                      const hasCode = !!r.stock_code;
                      const btn = (
                        <Button
                          size="small"
                          icon={<DownloadOutlined />}
                          disabled={!hasCode}
                          onClick={() => openDownloadModal(r)}
                        >
                          下载
                        </Button>
                      );
                      if (!hasCode) {
                        return (
                          <Tooltip title="请先在 mapping.json / 数据库补全 6 位股票代码">
                            <span>{btn}</span>
                          </Tooltip>
                        );
                      }
                      return btn;
                    },
                  },
                ]}
              />
            )}
          </Card>
        </Col>

        <Col xs={24} md={12}>
          <Card title="上传年报 PDF">
            {!company ? (
              <Alert
                type="info"
                showIcon
                message="请先在顶部选择公司，再上传"
              />
            ) : (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Typography.Text>
                  当前目标：<b>{company}</b> · <b>{year}</b> 年报
                </Typography.Text>
                <Button
                  type="primary"
                  icon={<InboxOutlined />}
                  onClick={() => setUploadOpen(true)}
                >
                  选择 PDF 上传
                </Button>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  上传后状态为 pending，需要在「解析」页触发 PDF→MD。
                </Typography.Text>
              </Space>
            )}
          </Card>
        </Col>
      </Row>

      {/* 新建公司 */}
      <Modal
        title="新建公司"
        open={newOpen}
        onOk={onCreate}
        onCancel={() => setNewOpen(false)}
        confirmLoading={create.isPending}
        destroyOnClose
      >
        <Form form={newForm} layout="vertical" preserve={false}>
          <Form.Item
            name="name"
            label="公司中文名"
            rules={[{ required: true, message: "请输入公司名" }, { max: 64 }]}
            extra="会自动从 mapping.json 查股票代码"
          >
            <Input placeholder="例如：宁德时代" autoFocus />
          </Form.Item>
        </Form>
      </Modal>

      {/* 上传 PDF */}
      <Modal
        title={`上传 ${company || "—"} ${year} 年报`}
        open={uploadOpen}
        onOk={onUpload}
        onCancel={() => setUploadOpen(false)}
        confirmLoading={uploading}
        destroyOnClose
      >
        <Form layout="vertical">
          <Form.Item label="年份" required>
            <InputNumber
              min={1990}
              max={2100}
              value={year}
              disabled
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item label="PDF 文件" required>
            <Upload.Dragger
              beforeUpload={(f) => {
                setFile(f);
                return false;
              }}
              maxCount={1}
              accept=".pdf"
              fileList={
                file ? [{ uid: "-1", name: file.name, status: "done" }] : []
              }
              onRemove={() => setFile(null)}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽 PDF 到此处</p>
            </Upload.Dragger>
          </Form.Item>
        </Form>
      </Modal>

      {/* 在线下载年报 */}
      <Modal
        title={`下载 ${downloadTarget?.name ?? "—"} 的年报`}
        open={downloadOpen}
        onOk={onConfirmDownload}
        onCancel={() => setDownloadOpen(false)}
        confirmLoading={downloading}
        okText="开始下载"
        cancelText="取消"
        destroyOnClose
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Typography.Text>
            将通过交易所站点自动下载并保存到
            <Typography.Text code>{downloadTarget?.name}/pdf/original/</Typography.Text>
            。每份年报约 1-15 MB，预计 30-60 秒/份。
          </Typography.Text>
          <div>
            <div style={{ marginBottom: 6 }}>选择要下载的年份（默认最近 3 年）：</div>
            <Checkbox.Group
              options={yearOptions.map((y) => ({ label: `${y}`, value: y }))}
              value={selectedYears}
              onChange={(v) => setSelectedYears(v as number[])}
            />
          </div>
        </Space>
      </Modal>

      {/* 下载进度 Drawer */}
      <Drawer
        title={
          taskStream.isDone
            ? `下载${taskStream.status === "done" ? "完成" : "失败"} — run #${runId ?? ""}`
            : `下载中… — run #${runId ?? ""}`
        }
        open={progressOpen}
        onClose={closeProgress}
        width={520}
        maskClosable={false}
        extra={
          <Button type="primary" onClick={closeProgress}>
            {taskStream.isDone ? "关闭" : "后台运行"}
          </Button>
        }
      >
        {taskStream.events.length === 0 ? (
          <Empty description="等待后端事件…" />
        ) : (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            {taskStream.events.map((ev) => {
              const icon =
                ev.level === "error" ? (
                  <CloseCircleTwoTone twoToneColor="#cf1322" />
                ) : ev.level === "warning" ? (
                  <CloseCircleTwoTone twoToneColor="#faad14" />
                ) : taskStream.isDone && ev === taskStream.events[taskStream.events.length - 1] ? (
                  <CheckCircleTwoTone twoToneColor="#52c41a" />
                ) : (
                  <LoadingOutlined />
                );
              return (
                <div
                  key={ev.id}
                  style={{
                    padding: "6px 8px",
                    borderBottom: "1px solid #f0f0f0",
                    fontSize: 13,
                  }}
                >
                  <Space>
                    {icon}
                    {ev.stage != null && <Tag color="blue">stage {ev.stage}</Tag>}
                    <span>{ev.message}</span>
                  </Space>
                </div>
              );
            })}
          </Space>
        )}
      </Drawer>
    </Space>
  );
}
