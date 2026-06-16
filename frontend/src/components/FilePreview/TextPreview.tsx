interface Props {
  content: string;
}

export default function TextPreview({ content }: Props) {
  return (
    <pre
      style={{
        margin: 0,
        padding: "12px 16px",
        fontFamily:
          "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        fontSize: 12.5,
        lineHeight: 1.6,
        background: "#fafafa",
        borderRadius: 6,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        color: "rgba(0, 0, 0, 0.88)",
      }}
    >
      {content}
    </pre>
  );
}
