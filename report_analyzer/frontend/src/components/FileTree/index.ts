import ParseYearFileTree from "./ParseYearFileTree";
import AnalysisFileTree from "./AnalysisFileTree";

// 解析阶段（按年）：章节 / 第三节 H2 / 抽取表格
// 分析阶段（公司级）：研究报告 / 合并表格
export { ParseYearFileTree, AnalysisFileTree };

// 默认导出保持解析视图，便于旧 import 路径工作
export default ParseYearFileTree;
