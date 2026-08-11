<script setup lang="ts">
/**
 * The project & conversation sheet.
 *
 * Lifted out of App.vue unchanged: the markup below is byte-for-byte what the
 * shell rendered before, and every binding it reads arrives through the
 * ProjectsContext injection rather than being re-declared here. Nothing about
 * how this renders should differ -- see tests/baseline/README.md for the
 * element-level diff that proves it.
 */
import {
  AppWindow,
  Archive,
  Bot,
  CheckCircle2,
  File as FileIcon,
  Folder as FolderIcon,
  FolderPlus,
  Gamepad2,
  Globe2,
  MessageCircle,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-vue-next'
import { DialogClose, DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogTitle } from 'reka-ui'
import { inject } from 'vue'
import { ProjectsContextKey } from '../../shellContext'

const ctx = inject(ProjectsContextKey)
if (!ctx) throw new Error('ContextRail must be rendered inside a shell that provides ProjectsContext')

const {
  connected,
  characterName,
  activeProject,
  activeThread,
  activeContext,
  visibleProjects,
  visibleThreads,
  resourceBindings,
  contextSearch,
  contextRailBusy,
  showArchivedContext,
  newProjectName,
  editingRailItem,
  editingRailValue,
  switchProject,
  activateThread,
  createProjectFromRail,
  createThreadFromRail,
  archiveProjectFromRail,
  archiveThreadFromRail,
  deleteArchivedProject,
  deleteArchivedThread,
  beginRailRename,
  saveRailRename,
  bindProjectDirectory,
  addTextResourceBinding,
  removeResourceBinding,
} = ctx
</script>

<template>
  <DialogPortal>
    <DialogOverlay class="context-rail-backdrop" />
    <DialogContent class="context-rail" aria-label="项目与对话导航">
      <header class="context-rail-head">
        <div>
          <span>共同在场</span>
          <DialogTitle as="strong">{{ activeProject?.name || '默认项目' }}</DialogTitle>
          <DialogDescription as="small">{{ activeThread?.title || '原有对话' }}</DialogDescription>
        </div>
        <DialogClose aria-label="关闭侧栏"><X :size="17" /></DialogClose>
      </header>

      <label class="context-search">
        <Search :size="15" />
        <input v-model="contextSearch" type="search" placeholder="搜索对话" />
      </label>

      <div class="context-primary-actions">
        <button type="button" @click="createThreadFromRail" :disabled="contextRailBusy" :aria-busy="contextRailBusy">
          <Plus :size="16" />
          新对话
        </button>
      </div>

      <div class="context-rail-scroll">
        <section class="context-group">
          <div class="context-group-title">
            <span>项目</span>
            <button type="button" :aria-pressed="showArchivedContext" @click="showArchivedContext = !showArchivedContext">
              {{ showArchivedContext ? '隐藏归档' : '归档' }}
            </button>
          </div>
          <div class="context-list">
            <article
              v-for="project in visibleProjects"
              :key="project.id"
              class="context-row project-row"
              :class="{ active: project.id === activeContext.project_id, archived: project.archived }"
            >
              <button type="button" class="context-row-main" @click="switchProject(project.id)">
                <FolderIcon :size="16" />
                <span v-if="editingRailItem?.type !== 'project' || editingRailItem.id !== project.id">{{ project.name }}</span>
              </button>
              <form
                v-if="editingRailItem?.type === 'project' && editingRailItem.id === project.id"
                class="context-inline-edit"
                @submit.prevent="saveRailRename"
              >
                <input v-model="editingRailValue" maxlength="80" autofocus />
                <button type="submit"><CheckCircle2 :size="15" /></button>
              </form>
              <div class="context-row-actions">
                <button type="button" title="重命名" @click="beginRailRename('project', project.id, project.name)"><Pencil :size="14" /></button>
                <button type="button" :title="project.archived ? '恢复项目' : '归档项目'" @click="archiveProjectFromRail(project)"><Archive :size="14" /></button>
                <button v-if="project.archived" type="button" title="永久删除" class="danger" @click="deleteArchivedProject(project)"><Trash2 :size="14" /></button>
              </div>
            </article>
          </div>
          <form class="context-new-project" @submit.prevent="createProjectFromRail">
            <Plus :size="15" />
            <input v-model="newProjectName" maxlength="80" placeholder="新项目名称" />
            <button type="submit" :disabled="!newProjectName.trim() || contextRailBusy" :aria-busy="contextRailBusy">创建</button>
          </form>
        </section>

        <section class="context-group">
          <div class="context-group-title">
            <span>对话</span>
            <small>{{ visibleThreads.length }}</small>
          </div>
          <div class="context-list thread-list">
            <article
              v-for="thread in visibleThreads"
              :key="thread.id"
              class="context-row thread-row"
              :class="{ active: thread.id === activeContext.thread_id, archived: thread.archived }"
            >
              <button type="button" class="context-row-main" @click="activateThread(thread.id)">
                <MessageCircle :size="15" />
                <span v-if="editingRailItem?.type !== 'thread' || editingRailItem.id !== thread.id">{{ thread.title }}</span>
              </button>
              <form
                v-if="editingRailItem?.type === 'thread' && editingRailItem.id === thread.id"
                class="context-inline-edit"
                @submit.prevent="saveRailRename"
              >
                <input v-model="editingRailValue" maxlength="100" autofocus />
                <button type="submit"><CheckCircle2 :size="15" /></button>
              </form>
              <div class="context-row-actions">
                <button type="button" title="重命名" @click="beginRailRename('thread', thread.id, thread.title)"><Pencil :size="14" /></button>
                <button type="button" :title="thread.archived ? '恢复对话' : '归档对话'" @click="archiveThreadFromRail(thread)"><Archive :size="14" /></button>
                <button v-if="thread.archived" type="button" title="永久删除" class="danger" @click="deleteArchivedThread(thread)"><Trash2 :size="14" /></button>
              </div>
            </article>
            <p v-if="!visibleThreads.length" class="context-empty">没有匹配的对话。</p>
          </div>
        </section>

        <section class="context-group context-bindings">
          <div class="context-group-title"><span>项目资源</span><small>{{ resourceBindings.length }}</small></div>
          <div class="binding-chips">
            <span v-for="binding in resourceBindings" :key="binding.id">
              <FolderIcon v-if="binding.kind === 'directory'" :size="13" />
              <Globe2 v-else-if="binding.kind === 'domain'" :size="13" />
              <Bot v-else-if="binding.kind === 'game'" :size="13" />
              <FileIcon v-else :size="13" />
              {{ binding.label }}
              <button type="button" :aria-label="`移除 ${binding.label}`" @click="removeResourceBinding(binding.id)"><X :size="12" /></button>
            </span>
            <small v-if="!resourceBindings.length">Joi 只会在已绑定范围内协作。</small>
          </div>
          <div class="binding-actions">
            <button type="button" @click="bindProjectDirectory"><FolderPlus :size="14" />文件夹</button>
            <button type="button" @click="addTextResourceBinding('domain')"><Globe2 :size="14" />网站</button>
            <button type="button" @click="addTextResourceBinding('application')"><AppWindow :size="14" />应用</button>
            <button type="button" @click="addTextResourceBinding('game')"><Gamepad2 :size="14" />游戏</button>
          </div>
        </section>
      </div>

      <footer class="context-rail-foot">
        <span :class="{ online: connected }"></span>
        {{ connected ? `${characterName} 在这个对话里` : '正在重新连接 Joi' }}
      </footer>
    </DialogContent>
  </DialogPortal>
</template>
