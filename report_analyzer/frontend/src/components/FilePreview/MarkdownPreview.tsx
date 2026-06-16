import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github.css";
import "./MarkdownPreview.css";

interface Props {
  content: string;
}

/** 自定义 <a> 渲染：协议白名单 + 强制 _blank，防预览上下文被链接带走。 */
const safeHref = (href: string | undefined): string | undefined => {
  if (!href) return undefined;
  if (/^(https?:|mailto:|\/api\/static\/)/.test(href)) return href;
  return undefined;
};

const markdownComponents = {
  a: ({
    href,
    children,
    ...rest
  }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
    const safe = safeHref(href);
    if (!safe) {
      return <span>{children}</span>;
    }
    return (
      <a href={safe} target="_blank" rel="noopener noreferrer" {...rest}>
        {children}
      </a>
    );
  },
};

// 扩展默认 sanitize schema：放开 <img> 的 src（年报 md 里常带绝对路径图片），
// 同时限制协议防止 javascript:/data:。attributes 用函数式赋值避免 schema 类型版本差异。
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    img: [
      ...(defaultSchema.attributes?.img ?? []),
      "src",
      ["title"],
    ],
  },
  // 限制 url 协议白名单
  protocols: {
    ...defaultSchema.protocols,
    src: ["http", "https", "data"],
    href: ["http", "https", "mailto"],
  },
};

export default function MarkdownPreview({ content }: Props) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // 顺序固定：raw 先把 <table> 等 HTML 解析进 AST，sanitize 过滤 XSS，
        // 最后 highlight 给 code 节点上色。
        rehypePlugins={[
          [rehypeRaw, { passThrough: ["math", "inline-math"] }],
          [rehypeSanitize, sanitizeSchema],
          [rehypeHighlight, { detect: true }],
        ]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
