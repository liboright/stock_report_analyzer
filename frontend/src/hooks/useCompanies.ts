import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { companiesApi } from "../api/companies";

export const useCompanies = () =>
  useQuery({ queryKey: ["companies"], queryFn: companiesApi.list });

export const useCompany = (name: string | undefined) =>
  useQuery({
    queryKey: ["company", name],
    queryFn: () => companiesApi.get(name!),
    enabled: !!name,
  });

export const useCreateCompany = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => companiesApi.create(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["companies"] });
    },
  });
};
