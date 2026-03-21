import { createApp } from 'vue';
import { createRouter, createWebHashHistory } from 'vue-router';

import TopBar from '/static/components/TopBar.js';
import SideNav from '/static/components/SideNav.js';
import ToastContainer from '/static/components/Toast.js';

import Dashboard from '/static/views/Dashboard.js';
import ChatView from '/static/views/ChatView.js';
import AgentList from '/static/views/AgentList.js';
import ToolList from '/static/views/ToolList.js';
import SkillList from '/static/views/SkillList.js';
import WorkflowList from '/static/views/WorkflowList.js';
import WorkflowBuilder from '/static/views/WorkflowBuilder.js';
import AppList from '/static/views/AppList.js';
import ScheduleList from '/static/views/ScheduleList.js';
import ApprovalCenter from '/static/views/ApprovalCenter.js';
import HubView from '/static/views/HubView.js';
import GovernanceDashboard from '/static/views/GovernanceDashboard.js';
import PolicyEditor from '/static/views/PolicyEditor.js';
import DebugPanel from '/static/views/DebugPanel.js';
import Settings from '/static/views/Settings.js';

const routes = [
  { path: '/', component: Dashboard },
  { path: '/chat', component: ChatView },
  { path: '/agents', component: AgentList },
  { path: '/tools', component: ToolList },
  { path: '/skills', component: SkillList },
  { path: '/workflows', component: WorkflowList },
  { path: '/workflows/builder', component: WorkflowBuilder },
  { path: '/workflows/builder/:name', component: WorkflowBuilder },
  { path: '/apps', component: AppList },
  { path: '/hub', component: HubView },
  { path: '/schedules', component: ScheduleList },
  { path: '/approvals', component: ApprovalCenter },
  { path: '/governance', component: GovernanceDashboard },
  { path: '/governance/policy', component: PolicyEditor },
  { path: '/debug', component: DebugPanel },
  { path: '/settings', component: Settings },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes,
});

const App = {
  components: { TopBar, SideNav, ToastContainer },
  template: `
    <div class="mx-layout">
      <TopBar />
      <div class="mx-body">
        <SideNav />
        <main class="mx-main">
          <router-view v-slot="{ Component }">
            <transition name="mx-fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </main>
      </div>
      <ToastContainer />
            </div>
  `,
};

const app = createApp(App);
app.use(router);
app.mount('#app');
