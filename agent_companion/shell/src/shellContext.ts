/**
 * Typed seams between App.vue and the panels split out of it.
 *
 * App.vue reached 6182 lines because every panel's markup had to live wherever
 * its state lived. Breaking that up by *moving state* first would be the wrong
 * order: functions like `activateThread` touch the Core client, conversation
 * history, capability sessions and the active cabin in a single body, so
 * relocating any one of them creates a second source of truth for the others
 * while the split is half done.
 *
 * So the state does not move. App.vue keeps owning it and hands each extracted
 * panel exactly the bindings that panel reads, through a typed injection key.
 * The template shrinks immediately, nothing changes at runtime, and each
 * context here is a written-down inventory of what that panel actually
 * depends on -- which is the thing you need before state can move later.
 */

import type { ComputedRef, InjectionKey, Ref } from 'vue'
import type { CollaborationSnapshot, JoiProject, JoiThread, ResourceBinding } from './protocol'

/**
 * What the project & conversation sheet reads from the shell.
 *
 * Written to match the shell's real signatures rather than tidied ones: the
 * first draft of this interface guessed, and `vue-tsc` rejected nine of the
 * entries. Keeping it honest is the point -- this is the inventory that has to
 * hold when the state eventually moves.
 */
export interface ProjectsContext {
  connected: Ref<boolean>
  characterName: ComputedRef<string>

  // `find()` returns undefined, not null.
  activeProject: ComputedRef<JoiProject | undefined>
  activeThread: ComputedRef<JoiThread | undefined>
  activeContext: Ref<NonNullable<CollaborationSnapshot['active']>>
  visibleProjects: ComputedRef<JoiProject[]>
  visibleThreads: ComputedRef<JoiThread[]>
  resourceBindings: Ref<ResourceBinding[]>

  contextSearch: Ref<string>
  contextRailBusy: Ref<boolean>
  showArchivedContext: Ref<boolean>
  newProjectName: Ref<string>
  editingRailItem: Ref<{ type: 'project' | 'thread'; id: string } | null>
  editingRailValue: Ref<string>

  // Some of these take an id and some take the whole row. That inconsistency
  // is pre-existing; recording it truthfully beats papering over it here.
  switchProject: (projectId: string) => Promise<void>
  activateThread: (threadId: string) => Promise<void>
  createProjectFromRail: () => Promise<void>
  createThreadFromRail: () => Promise<void>
  archiveProjectFromRail: (project: JoiProject) => Promise<void>
  archiveThreadFromRail: (thread: JoiThread) => Promise<void>
  deleteArchivedProject: (project: JoiProject) => Promise<void>
  deleteArchivedThread: (thread: JoiThread) => Promise<void>
  beginRailRename: (type: 'project' | 'thread', id: string, value: string) => void
  saveRailRename: () => Promise<void>
  bindProjectDirectory: () => Promise<void>
  addTextResourceBinding: (kind: 'domain' | 'application' | 'game') => Promise<void>
  removeResourceBinding: (bindingId: string) => Promise<void>
}

export const ProjectsContextKey: InjectionKey<ProjectsContext> = Symbol('joi.projects')

/** What the native file picker may be asked for. The Rust side keys on these. */
export type AttachmentKind = 'file' | 'folder'
