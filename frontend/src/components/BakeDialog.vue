<template>
  <Dialog v-model="open" :options="{ title: `Bake ${recipe.name}`, size: 'lg' }">
    <template #body-content>
      <div class="space-y-4">
        <p class="text-p-sm text-ink-gray-6">
          Configure inputs and produce a snapshot from
          <span class="font-medium text-ink-gray-8">{{ recipe.base_image }}</span
          >.
        </p>

        <!-- Generated inputs -->
        <div v-if="fields.length" class="space-y-4">
          <div v-for="field in fields" :key="field.key">
            <FormControl
              v-model="values[field.key]"
              :type="field.control"
              :label="field.label"
              :options="field.options"
              :placeholder="field.placeholder"
            />
            <p v-if="field.description" class="mt-1 text-xs text-ink-gray-5">
              {{ field.description }}
            </p>
          </div>
        </div>
        <p v-else class="rounded-md bg-surface-gray-1 px-3 py-2 text-p-sm text-ink-gray-5">
          This recipe takes no inputs.
        </p>

        <div class="h-px bg-outline-gray-2" />

        <!-- Per-bake release overrides -->
        <div v-if="trackedReleases.length" class="space-y-4">
          <div>
            <p class="text-sm font-medium text-ink-gray-8">Releases</p>
            <p class="text-xs text-ink-gray-5">Override tracked pins for this bake only.</p>
          </div>
          <div v-for="release in trackedReleases" :key="release.repo">
            <FormControl
              v-model="releaseOverrides[release.repo]"
              type="text"
              :label="release.repo"
              :placeholder="release.ref || 'not pinned'"
            />
          </div>
        </div>
        <div v-if="trackedReleases.length" class="h-px bg-outline-gray-2" />

        <!-- Mode + builder -->
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <FormControl v-model="mode" type="select" label="Mode" :options="modeOptions" />
            <p class="mt-1 text-xs text-ink-gray-5">What snapshot(s) to produce.</p>
          </div>
          <div>
            <FormControl v-model="builder" type="select" label="Builder" :options="builderOptions" />
            <p class="mt-1 text-xs text-ink-gray-5">Override the default builder.</p>
          </div>
        </div>

        <!-- Validation result -->
        <div
          v-if="validation"
          class="rounded-md px-3 py-2 text-p-sm"
          :class="
            validation.ok
              ? 'bg-surface-green-2 text-ink-green-8'
              : 'bg-surface-red-2 text-ink-red-8'
          "
        >
          <template v-if="validation.ok">Inputs look valid.</template>
          <template v-else>
            <p class="font-medium">Validation failed:</p>
            <ul class="mt-1 list-disc pl-4">
              <li v-for="(err, i) in validation.errors" :key="i">
                <span v-if="err.field" class="font-medium">{{ err.field }}: </span>{{ err.message }}
              </li>
            </ul>
          </template>
        </div>

        <ErrorMessage :message="error" />

        <!-- Footer -->
        <div class="flex items-center justify-end gap-2 pt-1">
          <Button label="Cancel" variant="subtle" @click="open = false" />
          <Button label="Validate" variant="outline" :loading="validating" @click="validate" />
          <Button label="Start bake" variant="solid" theme="gray" :loading="baking" @click="submit" />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Button, Dialog, ErrorMessage, FormControl } from 'frappe-ui'
import { recipesApi } from '@/api/recipes'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  recipe: { type: Object, required: true },
})
const emit = defineEmits(['update:modelValue', 'baked'])

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const values = reactive({})
const releaseOverrides = reactive({})
const mode = ref('cold')
const builder = ref('')
const error = ref('')
const validation = ref(null)
const validating = ref(false)
const baking = ref(false)

// Build a flat field list from the recipe's JSON-Schema `input_schema`.
const fields = computed(() => {
  const schema = props.recipe.input_schema || {}
  const properties = schema.properties || {}
  return Object.entries(properties).map(([key, prop]) => {
    const hasEnum = Array.isArray(prop.enum) && prop.enum.length
    let control = 'text'
    let options
    if (hasEnum) {
      control = 'select'
      options = prop.enum.map((v) => ({ label: String(v), value: v }))
    } else if (prop.type === 'integer' || prop.type === 'number') {
      control = 'number'
    } else if (prop.type === 'boolean') {
      control = 'checkbox'
    }
    return {
      key,
      control,
      options,
      type: prop.type,
      label: prop.title || key,
      description: prop.description || '',
      placeholder: prop.default != null ? `Default: ${prop.default}` : '',
      default: prop.default,
    }
  })
})

const modeOptions = computed(() =>
  (props.recipe.modes || ['cold']).map((m) => ({ label: m, value: m })),
)

const builderOptions = [
  { label: 'Default', value: '' },
  { label: 'docker', value: 'docker' },
  { label: 'local', value: 'local' },
  { label: 'atlas', value: 'atlas' },
]

const trackedReleases = computed(() => props.recipe.tracked || [])

// (Re)seed the form whenever the dialog opens.
watch(
  () => props.modelValue,
  (isOpen) => {
    if (!isOpen) return
    error.value = ''
    validation.value = null
    for (const field of fields.value) {
      values[field.key] =
        field.default != null ? field.default : field.control === 'checkbox' ? false : ''
    }
    const tracked = props.recipe.tracked || []
    for (const key of Object.keys(releaseOverrides)) {
      if (!tracked.some((release) => release.repo === key)) delete releaseOverrides[key]
    }
    for (const release of tracked) {
      releaseOverrides[release.repo] = release.ref || ''
    }
    const modes = props.recipe.modes || ['cold']
    mode.value = modes.includes(mode.value) ? mode.value : modes[0]
  },
  { immediate: true },
)

// Coerce form values back to the types the schema declares.
function collectInputs() {
  const inputs = {}
  for (const field of fields.value) {
    const raw = values[field.key]
    if (field.control === 'checkbox') {
      inputs[field.key] = Boolean(raw)
    } else if (field.control === 'number') {
      if (raw === '' || raw == null) continue
      inputs[field.key] = Number(raw)
    } else {
      if (raw === '' || raw == null) continue
      inputs[field.key] = raw
    }
  }
  return inputs
}

// Per-bake release overrides: only repos whose field was changed from the current pin.
function collectReleases() {
  const releases = {}
  for (const release of props.recipe.tracked || []) {
    const override = String(releaseOverrides[release.repo] || '').trim()
    if (override && override !== (release.ref || '')) releases[release.repo] = override
  }
  return releases
}

async function validate() {
  validating.value = true
  error.value = ''
  validation.value = null
  try {
    validation.value = await recipesApi.validate(
      props.recipe.name, collectInputs(), collectReleases(),
    )
  } catch (caught) {
    error.value = caught.message || 'Validation request failed'
  } finally {
    validating.value = false
  }
}

async function submit() {
  baking.value = true
  error.value = ''
  try {
    const releases = collectReleases()
    const payload = { inputs: collectInputs(), mode: mode.value }
    if (Object.keys(releases).length) payload.releases = releases
    if (builder.value) payload.builder = builder.value
    const result = await recipesApi.bake(props.recipe.name, payload)
    emit('baked', result.bake_id)
    open.value = false
  } catch (caught) {
    error.value = caught.message || 'Failed to start bake'
  } finally {
    baking.value = false
  }
}
</script>
