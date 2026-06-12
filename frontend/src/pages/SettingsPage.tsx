import { Alert, Card, Descriptions, Spin, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { settingsApi } from "../api/settings";

export default function SettingsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["settings"],
    queryFn: settingsApi.get,
  });

  if (isLoading) return <Spin />;
  if (error || !data) return <Alert type="error" message="加载设置失败" />;

  return (
    <Card title="系统设置">
      <Descriptions
        bordered
        column={1}
        size="small"
        items={[
          { key: "report_base_path", label: "报告基础路径", children: data.report_base_path },
          { key: "raw_base_path", label: "原始文件路径", children: data.raw_base_path },
          { key: "script_path", label: "脚本路径", children: data.script_path },
          { key: "mapping_path", label: "公司映射", children: data.mapping_path },
          { key: "db_path", label: "数据库", children: data.db_path },
          { key: "log_dir", label: "日志目录", children: data.log_dir },
          {
            key: "anthropic",
            label: "Anthropic API Key",
            children: data.anthropic_key_set ? <Tag color="green">已设置</Tag> : <Tag color="red">未设置</Tag>,
          },
          {
            key: "mineru",
            label: "MinerU API Key",
            children: data.mineru_key_set ? <Tag color="green">已设置</Tag> : <Tag color="red">未设置</Tag>,
          },
          ...(data.anthropic_model
            ? [{ key: "anthropic_model", label: "Anthropic 模型", children: data.anthropic_model }]
            : []),
          ...(data.host && data.port
            ? [{ key: "hostport", label: "监听", children: `${data.host}:${data.port}` }]
            : []),
        ]}
      />
    </Card>
  );
}
