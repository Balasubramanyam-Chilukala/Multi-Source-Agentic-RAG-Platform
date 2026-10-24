import type { Role } from "@/api/types";

const RANK: Record<Role, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };

export function hasRole(role: Role | undefined | null, minimum: Role): boolean {
  if (!role || !(role in RANK)) return false;
  return RANK[role] >= RANK[minimum];
}

/** What each role may do in the UI (mirrors the backend's MemberDep / AdminDep checks). */
export const permissions = {
  uploadDocuments: (role?: Role | null) => hasRole(role, "member"),
  deleteDocuments: (role?: Role | null) => hasRole(role, "member"),
  createCollections: (role?: Role | null) => hasRole(role, "member"),
  renameCollections: (role?: Role | null) => hasRole(role, "member"),
  deleteCollections: (role?: Role | null) => hasRole(role, "admin"),
  reembed: (role?: Role | null) => hasRole(role, "admin"),
  changeSettings: (role?: Role | null) => hasRole(role, "admin"),
  runEvaluations: (role?: Role | null) => hasRole(role, "admin"),
};

export type Permission = keyof typeof permissions;
