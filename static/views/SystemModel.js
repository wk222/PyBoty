import { computed, onMounted, ref } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';

export default {
  name: 'SystemModelView',
  setup() {
    const loading = ref(true);
    const model = ref(null);

    async function loadModel() {
      loading.value = true;
      try {
        model.value = await API.getSystemModel();
      } catch (error) {
        toast('Failed to load system model: ' + error.message, 'error');
      } finally {
        loading.value = false;
      }
    }

    onMounted(loadModel);

    const interactionSurfaces = computed(() => model.value?.interaction_surfaces || []);
    const ecosystemFamilies = computed(() => model.value?.ecosystem_families || []);
    const rootModes = computed(() => model.value?.root_modes || []);
    const productConcepts = computed(() => model.value?.product_concepts || []);
    const supportingSystems = computed(() => model.value?.supporting_systems || []);
    const internalDomains = computed(() => model.value?.internal_domains || []);
    const packageTargets = computed(() => model.value?.package_targets || []);
    const antiSprawlQuestions = computed(() => model.value?.anti_sprawl_questions || []);
    const notProductConcepts = computed(() => model.value?.not_product_concepts || []);
    const canonicalRules = computed(() => model.value?.canonical_rules || []);

    function s(key) {
      return t('systemModel.' + key);
    }

    return {
      loading,
      model,
      interactionSurfaces,
      ecosystemFamilies,
      rootModes,
      productConcepts,
      supportingSystems,
      internalDomains,
      packageTargets,
      antiSprawlQuestions,
      notProductConcepts,
      canonicalRules,
      loadModel,
      s,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <div>
          <h1 class="mx-page-title">{{ s('title') }}</h1>
          <p style="margin:6px 0 0;color:var(--text-secondary);max-width:880px;line-height:1.6;">
            {{ s('subtitle') }}
          </p>
        </div>
        <button class="mx-btn mx-btn--ghost" @click="loadModel">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          {{ s('refresh') }}
        </button>
      </div>

      <div v-if="loading" class="mx-loading">
        <div class="mx-spinner"></div>
        <span>{{ s('loading') }}</span>
      </div>

      <div v-else-if="model" style="display:grid;gap:20px;">
        <section class="mx-section">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;">
            <div>
              <h2 class="mx-section-title">{{ s('northStar') }}</h2>
              <p style="margin:8px 0 0;color:var(--text-secondary);line-height:1.7;max-width:920px;">
                {{ model.north_star }}
              </p>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <span
                v-for="mode in model.root_mode_progression"
                :key="mode"
                style="padding:6px 10px;border-radius:999px;background:var(--bg-tertiary);border:1px solid var(--border);font-size:12px;font-weight:700;color:var(--text-secondary);"
              >
                {{ mode }}
              </span>
            </div>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('interactionSurfaces') }}</h2>
          <div class="mx-stats-grid" style="margin-top:14px;">
            <article
              v-for="surface in interactionSurfaces"
              :key="surface.name"
              class="mx-stat-card mx-stat-card--static"
              style="align-items:flex-start;"
            >
              <div class="mx-stat-body" style="gap:10px;">
                <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;">
                  <div>
                    <div class="mx-stat-title">{{ surface.label }}</div>
                    <code style="display:inline-block;margin-top:6px;padding:4px 8px;border-radius:10px;background:var(--bg-tertiary);color:var(--text-secondary);font-size:12px;">{{ surface.route }}</code>
                  </div>
                </div>
                <div style="color:var(--text-secondary);line-height:1.6;">{{ surface.summary }}</div>
                <div style="display:flex;flex-wrap:wrap;gap:6px;">
                  <span
                    v-for="job in surface.primary_jobs"
                    :key="job"
                    style="padding:6px 10px;border-radius:999px;background:rgba(96,165,250,0.12);color:var(--text-primary);font-size:12px;"
                  >
                    {{ job }}
                  </span>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('ecosystemFamilies') }}</h2>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:14px;">
            <article
              v-for="family in ecosystemFamilies"
              :key="family.name"
              style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);"
            >
              <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;">
                <div>
                  <div style="font-size:16px;font-weight:700;color:var(--text-primary);">{{ family.label }}</div>
                  <div style="margin-top:4px;color:var(--text-secondary);line-height:1.6;">{{ family.summary }}</div>
                </div>
                <code style="padding:4px 8px;border-radius:10px;background:var(--bg-tertiary);color:var(--text-secondary);font-size:12px;">{{ family.manager_route }}</code>
              </div>
            </article>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('rootModes') }}</h2>
          <div class="mx-stats-grid" style="margin-top:14px;">
            <article
              v-for="mode in rootModes"
              :key="mode.name"
              class="mx-stat-card mx-stat-card--static"
              style="align-items:flex-start;"
            >
              <div class="mx-stat-body" style="gap:10px;">
                <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;">
                  <div>
                    <div class="mx-stat-title">{{ mode.label }}</div>
                    <div style="font-size:11px;color:var(--text-muted);margin-top:2px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;">
                      {{ mode.autonomy_level }}
                    </div>
                  </div>
                </div>
                <div style="color:var(--text-secondary);line-height:1.6;">{{ mode.summary }}</div>
                <div>
                  <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:6px;">{{ s('responsibilities') }}</div>
                  <div style="display:flex;flex-wrap:wrap;gap:6px;">
                    <span
                      v-for="item in mode.primary_responsibilities"
                      :key="item"
                      style="padding:6px 10px;border-radius:10px;background:rgba(129,140,248,0.10);color:var(--text-primary);font-size:12px;"
                    >
                      {{ item }}
                    </span>
                  </div>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('productConcepts') }}</h2>
          <div style="margin-top:14px;display:grid;gap:12px;">
            <article
              v-for="concept in productConcepts"
              :key="concept.name"
              style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);"
            >
              <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;">
                <div>
                  <div style="font-size:16px;font-weight:700;color:var(--text-primary);">{{ concept.label }}</div>
                  <div style="margin-top:4px;color:var(--text-muted);font-size:12px;">{{ concept.question }}</div>
                </div>
                <code style="padding:4px 8px;border-radius:10px;background:var(--bg-tertiary);color:var(--text-secondary);font-size:12px;">{{ concept.name }}</code>
              </div>
              <p style="margin:10px 0 0;color:var(--text-secondary);line-height:1.7;">{{ concept.responsibility }}</p>
              <div style="margin-top:12px;">
                <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:6px;">{{ s('examples') }}</div>
                <div style="display:flex;flex-wrap:wrap;gap:6px;">
                  <span
                    v-for="item in concept.capability_examples"
                    :key="item"
                    style="padding:6px 10px;border-radius:999px;background:rgba(52,211,153,0.10);color:var(--text-primary);font-size:12px;"
                  >
                    {{ item }}
                  </span>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('supportingSystems') }}</h2>
          <div style="margin-top:14px;display:grid;gap:12px;">
            <article
              v-for="system in supportingSystems"
              :key="system.name"
              style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);"
            >
              <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;">
                <div>
                  <div style="font-size:16px;font-weight:700;color:var(--text-primary);">{{ system.label }}</div>
                  <div style="margin-top:4px;color:var(--text-secondary);line-height:1.6;">{{ system.purpose }}</div>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;">
                  <span
                    v-for="conceptName in system.strengthens_concepts"
                    :key="conceptName"
                    style="padding:5px 8px;border-radius:10px;background:rgba(251,191,36,0.12);color:var(--text-primary);font-size:11px;font-weight:700;"
                  >
                    {{ conceptName }}
                  </span>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('internalDomains') }}</h2>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:14px;">
            <article
              v-for="domain in internalDomains"
              :key="domain.name"
              style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);"
            >
              <div style="font-size:15px;font-weight:700;color:var(--text-primary);">{{ domain.label }}</div>
              <p style="margin:8px 0 0;color:var(--text-secondary);line-height:1.6;">{{ domain.purpose }}</p>
            </article>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('packageTargets') }}</h2>
          <div style="display:grid;gap:12px;margin-top:14px;">
            <article
              v-for="target in packageTargets"
              :key="target.name"
              style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);"
            >
              <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap;">
                <div>
                  <div style="font-size:16px;font-weight:700;color:var(--text-primary);">{{ target.label }}</div>
                  <div style="margin-top:4px;color:var(--text-secondary);line-height:1.6;">{{ target.purpose }}</div>
                </div>
                <code style="padding:4px 8px;border-radius:10px;background:var(--bg-tertiary);color:var(--text-secondary);font-size:12px;">{{ target.path }}</code>
              </div>
              <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;">
                <span
                  v-for="scope in target.migration_scope"
                  :key="scope"
                  style="padding:6px 10px;border-radius:999px;background:rgba(96,165,250,0.12);color:var(--text-primary);font-size:12px;"
                >
                  {{ scope }}
                </span>
              </div>
            </article>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('guardrails') }}</h2>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:14px;">
            <div style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);">
              <div style="font-size:13px;font-weight:700;color:var(--text-primary);margin-bottom:10px;">{{ s('notProductConcepts') }}</div>
              <div style="display:flex;flex-wrap:wrap;gap:6px;">
                <span
                  v-for="item in notProductConcepts"
                  :key="item"
                  style="padding:6px 10px;border-radius:999px;background:rgba(244,114,182,0.10);color:var(--text-primary);font-size:12px;"
                >
                  {{ item }}
                </span>
              </div>
            </div>
            <div style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);">
              <div style="font-size:13px;font-weight:700;color:var(--text-primary);margin-bottom:10px;">{{ s('canonicalRules') }}</div>
              <div style="display:grid;gap:8px;">
                <div
                  v-for="rule in canonicalRules"
                  :key="rule"
                  style="padding:10px 12px;border-radius:12px;background:var(--bg-tertiary);color:var(--text-secondary);line-height:1.6;"
                >
                  {{ rule }}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="mx-section">
          <h2 class="mx-section-title">{{ s('antiSprawl') }}</h2>
          <div style="display:grid;gap:10px;margin-top:14px;">
            <div
              v-for="question in antiSprawlQuestions"
              :key="question"
              style="padding:14px 16px;border-radius:14px;border:1px solid var(--border);background:var(--bg-secondary);color:var(--text-secondary);line-height:1.7;"
            >
              {{ question }}
            </div>
          </div>
        </section>
      </div>
    </div>
  `,
};
