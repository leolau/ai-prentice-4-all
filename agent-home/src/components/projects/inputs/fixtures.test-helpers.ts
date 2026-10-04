import type { ProjectDetail, ProjectLink } from "@/types";

export const NOW = Math.floor(Date.now() / 1000);

export const LINK = (over: Partial<ProjectLink>): ProjectLink => ({
  project_id: "prj_1",
  kind: "url",
  profile: "default",
  ref: "https://example.com",
  label: null,
  added_by: "leo",
  added_at: NOW - 2 * 86_400,
  resolved: true,
  ...over,
});

/** Just the fields the Inputs tab reads. */
export const PROJECT = {
  id: "prj_1",
  slug: "mou",
  name: "ConnectAR MOUs",
  goal: "Draft the ConnectAR MOUs",
  description: "Four bilateral MOUs.",
  host_profile: "default",
  archived: false,
  links: {},
} as unknown as ProjectDetail;
