import { Layout, Menu, Typography } from "antd";
import { Link, Outlet, useLocation } from "react-router-dom";

const { Header, Content } = Layout;

const ITEMS = [
  { key: "search", path: "/search", label: "搜索/上传" },
  { key: "parse", path: "/parse", label: "解析" },
  { key: "analysis", path: "/analysis", label: "分析" },
  { key: "reports", path: "/reports", label: "报告" },
  { key: "settings", path: "/settings", label: "设置" },
];

function pickSelected(path: string): string {
  for (const it of ITEMS) {
    if (path === it.path || path.startsWith(it.path + "/")) return it.key;
  }
  return "search";
}

export default function AppLayout() {
  const loc = useLocation();
  const selected = pickSelected(loc.pathname);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", padding: "0 24px" }}>
        <Typography.Title level={4} style={{ color: "#fff", margin: 0, marginRight: 32 }}>
          A 股年报处理系统
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selected]}
          style={{ flex: 1, minWidth: 0 }}
          items={ITEMS.map((it) => ({
            key: it.key,
            label: <Link to={it.path}>{it.label}</Link>,
          }))}
        />
      </Header>
      <Content style={{ padding: 24, background: "#f0f2f5" }}>
        <Outlet />
      </Content>
    </Layout>
  );
}
