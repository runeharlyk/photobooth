<script lang="ts">
  import { fade } from 'svelte/transition'
  import { booth } from './lib/booth'
  import Countdown from './screens/Countdown.svelte'
  import Idle from './screens/Idle.svelte'
  import Result from './screens/Result.svelte'

  let innerWidth = $state(1280)
  let innerHeight = $state(800)

  const scale = $derived(Math.min(innerWidth / 1280, innerHeight / 800))
</script>

<svelte:window bind:innerWidth bind:innerHeight />

<main style:scale>
  {#if $booth}
    {#if $booth.screen === 'idle'}
      <Idle message={$booth.message} fault={$booth.fault} />
    {:else if $booth.screen === 'result'}
      <Result state={$booth} />
    {:else}
      <Countdown remaining={$booth.remaining} />
    {/if}
    {#if $booth.screen === 'capturing'}
      <div class="flash" out:fade={{ duration: 300 }}></div>
    {/if}
  {/if}
</main>

<style>
  main {
    position: absolute;
    left: calc(50% - var(--stage-w) / 2);
    top: calc(50% - var(--stage-h) / 2);
    width: var(--stage-w);
    height: var(--stage-h);
    overflow: hidden;
  }

  .flash {
    position: absolute;
    inset: 0;
    background: #fff;
  }
</style>
