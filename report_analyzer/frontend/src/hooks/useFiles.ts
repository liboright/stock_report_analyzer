import { useQuery } from "@tanstack/react-query";
import { filesApi } from "../api/files";

export const useFiles = (company: string | undefined, year: number | undefined) =>
  useQuery({
    queryKey: ["files", company, year],
    queryFn: () => filesApi.list(company!, year!),
    enabled: !!company && !!year,
  });
