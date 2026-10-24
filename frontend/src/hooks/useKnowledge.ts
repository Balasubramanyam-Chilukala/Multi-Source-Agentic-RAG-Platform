import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import type { CollectionCreate, CollectionUpdate, TextDocumentCreate, UrlDocumentCreate } from "@/api/types";
import { qk } from "./keys";
import { documentPollInterval, documentsListPollInterval } from "./polling";

export function useCollections() {
  return useQuery({ queryKey: qk.collections, queryFn: () => api.collections.list() });
}

export function useCollection(id: string) {
  return useQuery({ queryKey: qk.collection(id), queryFn: () => api.collections.get(id) });
}

function useInvalidateKnowledge() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: qk.collections });
    void client.invalidateQueries({ queryKey: qk.documentsAll });
    void client.invalidateQueries({ queryKey: qk.dashboard });
  };
}

export function useCreateCollection() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({ mutationFn: (body: CollectionCreate) => api.collections.create(body), onSuccess: invalidate });
}

export function useUpdateCollection() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CollectionUpdate }) => api.collections.update(id, body),
    onSuccess: invalidate,
  });
}

export function useDeleteCollection() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({ mutationFn: (id: string) => api.collections.remove(id), onSuccess: invalidate });
}

export function useReembedCollection() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({ mutationFn: (id: string) => api.collections.reembed(id), onSuccess: invalidate });
}

export function useDocuments(collectionId: string) {
  return useQuery({
    queryKey: qk.documents(collectionId),
    queryFn: () => api.documents.list({ collection_id: collectionId, limit: 100 }),
    // Keep polling the list while any document is still being ingested.
    refetchInterval: (query) => documentsListPollInterval(query.state.data?.items.map((d) => d.status)),
  });
}

export function useDocument(id: string | null) {
  return useQuery({
    queryKey: qk.document(id ?? ""),
    queryFn: () => api.documents.get(id as string),
    enabled: Boolean(id),
    refetchInterval: (query) => documentPollInterval(query.state.data?.status),
  });
}

export function useDocumentChunks(id: string | null, offset: number, limit = 10) {
  return useQuery({
    queryKey: qk.documentChunks(id ?? "", offset),
    queryFn: () => api.documents.chunks(id as string, { limit, offset }),
    enabled: Boolean(id),
    placeholderData: keepPreviousData,
  });
}

export function useChunk(id: string | null) {
  return useQuery({
    queryKey: qk.chunk(id ?? ""),
    queryFn: () => api.chunks.get(id as string),
    enabled: Boolean(id),
    staleTime: Infinity,
    retry: false,
  });
}

export function useUploadDocument() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({
    mutationFn: ({ collectionId, file, title }: { collectionId: string; file: File; title?: string }) =>
      api.documents.upload(collectionId, file, title),
    onSuccess: invalidate,
  });
}

export function useCreateTextDocument() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({ mutationFn: (body: TextDocumentCreate) => api.documents.createText(body), onSuccess: invalidate });
}

export function useCreateUrlDocument() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({ mutationFn: (body: UrlDocumentCreate) => api.documents.createUrl(body), onSuccess: invalidate });
}

export function useRetryDocument() {
  const invalidate = useInvalidateKnowledge();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.documents.retry(id),
    onSuccess: (_doc, id) => {
      invalidate();
      void client.invalidateQueries({ queryKey: qk.document(id) });
    },
  });
}

export function useDeleteDocument() {
  const invalidate = useInvalidateKnowledge();
  return useMutation({ mutationFn: (id: string) => api.documents.remove(id), onSuccess: invalidate });
}
