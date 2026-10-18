<script lang="ts">
  import Confetti from '../lib/Confetti.svelte'
  import Sticker from '../lib/Sticker.svelte'
  import { tap } from '../lib/booth'

  let {
    message,
    fault,
    notice,
  }: { message: string | null; fault: string | null; notice: string | null } = $props()

  let sending = $state(false)

  async function press() {
    if (sending) return
    sending = true
    await tap()
    sending = false
  }
</script>

<div class="idle">
  <Confetti opacity={0.85} />
  <button class="tap circle" class:inert={fault} aria-disabled={!!fault} style:--d="calc(var(--idle-d) * 1px)" onclick={press}>
    <span class="label"><Sticker tilt={-7} size={67} border={7} shadow={[8, 10]}>TAP HERE</Sticker></span>
  </button>
  <p class="helper">{fault ?? message ?? notice ?? 'Take a photo'}</p>
</div>

<style>
  .idle {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: calc(var(--idle-gap) * 1px);
  }

  .tap {
    position: relative;
    transition:
      transform 80ms ease-out,
      opacity 200ms;
  }

  .tap:not(.inert):active {
    transform: scale(0.94);
  }

  .tap.inert {
    opacity: 0.35;
  }

  .label {
    position: absolute;
    left: 50%;
    top: 50%;
    translate: -50% -50%;
  }
</style>
