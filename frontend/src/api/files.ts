import { api } from "./client";

export interface SubsectionFile {
  title: string;
  path: string;
}

export interface ChapterFile {
  section_num: string;
  title: string;
  path: string;
  subsections: SubsectionFile[];
}

export interface Section3File {
  title: string;
  path: string;
}

export interface ResearchFile {
  title: string;
  path: string;
}

export interface TableCsvFile {
  category: string; // 8 类之一 或 "其他" / "未分类"
  name: string;
  path: string;
}

export interface FileTree {
  chapters: ChapterFile[];
  section3: Section3File[];
  research: ResearchFile[];
  tables: TableCsvFile[];
}

export const filesApi = {
  list: (company: string, year: number) =>
    api
      .get<FileTree>(
        `/companies/${encodeURIComponent(company)}/reports/${year}/files`,
      )
      .then((r) => r.data),
};

/** 把后端返回的 posix 相对路径拼成前端可请求的 URL。中文段独立 encode。 */
export function getFileUrl(relPath: string): string {
  return (
    "/api/static/md/" +
    relPath
      .split("/")
      .map((seg) => encodeURIComponent(seg))
      .join("/")
  );
}
