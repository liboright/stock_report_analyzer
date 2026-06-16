import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Grid,
  Space,
  Spin,
  message,
} from "antd";
import { CopyOutlined, LinkOutlined } from "@ant-design/icons";
import { fetchText, getFileUrl } from "../../api/files";
import MarkdownPreview from "./MarkdownPreview";
import CsvPreview from "./CsvPreview";
import TextPreview from "./TextPreview";

export interface PreviewTarget {
  /** 后端文件树返回的相对 posix 路径（用作 fetch 标识） */
  path: string;
  /** 列表中显示的标题 */
  title: string;
  /** 'md' 走 /api/static/md/；'raw' 走 /api/static/raw/ */
  kind: "md" | "raw";
}

interface Props {
  value: PreviewTarget | null;
  onClose: () => void;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; content: string };

const detectFormat = (path: string): "md" | "csv" | "text" => {
  const l = path.toLowerCase();
  if (l.endsWith(".md") || l.endsWith(".markdown")) return "md";
  if (l.endsWith(".csv") || l.endsWith(".tsv")) return "csv";
  return "text";
};

export default function PreviewDrawer({ value, onClose }: Props) {
  const [state, setState] = useState<State>({ kind: "idle" });
  // 取消上一个 in-flight 请求，避免快速切换文件时旧响应覆盖新内容
  const abortRef = useRef<AbortController | null>(null);
  const contentRef = useRef<string>("");
  const screens = Grid.useBreakpoint();

  const open = !!value;

  useEffect(() => {
    if (!value) {
      abortRef.current?.abort();
      abortRef.current = null;
      setState({ kind: "idle" });
      contentRef.current = "";
      return;
    }

    const ctrl = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ctrl;

    setState({ kind: "loading" });
    fetchText(value.path, value.kind)
      .then((text) => {
        if (ctrl.signal.aborted) return;
        contentRef.current = text;
        setState({ kind: "ready", content: text });
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        const msg =
          (err as { response?: { data?: { detail?: string } } })?.response?.data
            ?.detail ??
          (err as { message?: string })?.message ??
          "未知错误";
        setState({ kind: "error", message: String(msg) });
      });

    return () => {
      ctrl.abort();
    };
  }, [value]);

  const width = screens.md ? 960 : "100%";

  const copySource = async () => {
    if (!contentRef.current) {
      message.warning("内容为空");
      return;
    }
    try {
      await navigator.clipboard.writeText(contentRef.current);
      message.success("已复制源内容到剪贴板");
    } catch {
      message.error("复制失败（浏览器拒绝）");
    }
  };

  const openInNewTab = () => {
    if (!value) return;
    window.open(getFileUrl(value.path), "_blank", "noopener,noreferrer");
  };

  const format = value ? detectFormat(value.path) : "text";
  const extra = value ? (
    <Space>
      <Button
        size="small"
        icon={<LinkOutlined />}
        onClick={openInNewTab}
        title="在浏览器新窗口打开（保留旧行为）"
      >
        新窗口
      </Button>
      <Button
        size="small"
        icon={<CopyOutlined />}
        onClick={copySource}
        disabled={state.kind !== "ready"}
      >
        复制源
      </Button>
    </Space>
  ) : null;

  return (
    <Drawer
      title={value?.title ?? "文件预览"}
      open={open}
      onClose={onClose}
      placement="right"
      width={width}
      destroyOnClose
      mask
      keyboard
      extra={extra}
      styles={{ body: { padding: 0 } }}
    >
      {state.kind === "loading" && (
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            minHeight: 240,
          }}
        >
          <Spin tip="加载中…" />
        </div>
      )}

      {state.kind === "error" && (
        <div style={{ padding: 16 }}>
          <Alert
            type="error"
            showIcon
            message="无法加载文件"
            description={state.message}
          />
        </div>
      )}

      {state.kind === "ready" && state.content.trim() === "" && (
        <div style={{ padding: 24 }}>
          <Empty description="文件为空" />
        </div>
      )}

      {state.kind === "ready" && state.content.trim() !== "" && (
        <div
          style={{
            maxHeight: "calc(100vh - 110px)",
            overflowY: "auto",
            padding: format === "md" ? "16px 24px" : 16,
          }}
        >
          {format === "md" && <MarkdownPreview content={state.content} />}
          {format === "csv" && <CsvPreview content={state.content} />}
          {format === "text" && <TextPreview content={state.content} />}
        </div>
      )}
    </Drawer>
  );
}
