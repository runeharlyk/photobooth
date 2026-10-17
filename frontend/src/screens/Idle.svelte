<script lang="ts">
  import Confetti from '../lib/Confetti.svelte'
  import Sticker from '../lib/Sticker.svelte'
  import { tap } from '../lib/booth'

  let { message, fault }: { message: string | null; fault: string | null } = $props()

  let sending = $state(false)

  async function press() {
    if (sending || fault) return
    sending = true
    await tap()
    sending = false
  }
</script>

<div class="idle">
  <Confetti opacity={0.85} />
  <button class="tap circle" class:inert={fault} style:--d="230px" onclick={press}>
    <span class="label"><Sticker tilt={-7} size={40}>TAP HERE</Sticker></span>
  </button>
  <p class:alert={fault || message}>{fault ?? message ?? 'Take a photo'}</p>
</div>

<style>
  .idle {
    position: absolute;
    inset: 0;
  }

  .tap {
    position: absolute;
    left: calc(50% - 115px);
    top: calc(50% - 115px);
    transition:
      transform 80ms ease-out,
      opacity 200ms;
  }

  .tap:active {
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

  p {
    position: absolute;
    left: 0;
    right: 0;
    top: calc(50% + 115px + 36px);
    margin: 0;
    text-align: center;
    color: var(--muted);
    font-size: 28px;
  }

  p.alert {
    color: #fff;
    font-weight: 800;
  }
</style>
