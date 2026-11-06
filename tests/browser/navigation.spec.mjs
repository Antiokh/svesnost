import { test, expect } from '@playwright/test';

test('mobile splash exposes language and theme controls in all editions', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile');
  for (const url of ['/', '/ru/', '/en/']) {
    await page.goto(url);
    const toolbar = page.locator('[data-sves-mobile-toolbar]');
    await expect(toolbar).toBeVisible();
    await expect(toolbar.locator('select[data-sves-language-mobile]')).toHaveCount(1);
    await expect(toolbar.locator('select[data-sves-theme-mobile]')).toHaveCount(1);
  }
  // Narrow devices must not push the controls off-screen.
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto('/');
  const box = await page.locator('[data-sves-mobile-toolbar]').boundingBox();
  expect(box).not.toBeNull();
  expect(box.x + box.width).toBeLessThanOrEqual(321);
});

test('mobile language switching and theme survive navigation and reload', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile');
  await page.goto('/');
  await page.locator('[data-sves-mobile-toolbar] select[data-sves-language-mobile]').selectOption('/ru/');
  await expect(page).toHaveURL(/\/ru\/$/);
  await page.locator('[data-sves-mobile-toolbar] select[data-sves-theme-mobile]').selectOption('light');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.locator('[data-sves-mobile-toolbar] select[data-sves-theme-mobile]').selectOption('auto');
  await expect(page.locator('[data-sves-mobile-toolbar] select[data-sves-theme-mobile]')).toHaveValue('auto');
});

test('mobile popover retains Escape and contains compact preferences', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile');
  await page.goto('/editorial-policy/');
  await page.locator('button[popovertarget="starlight__sidebar"]').click();
  const popover = page.locator('#starlight__sidebar');
  await expect.poll(() => popover.evaluate((node) => node.matches(':popover-open'))).toBe(true);
  await expect(popover.locator('.language-mobile')).toBeVisible();
  await expect(popover.locator('.theme-mobile')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect.poll(() => popover.evaluate((node) => node.matches(':popover-open'))).toBe(false);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(() => popover.evaluate((node) => getComputedStyle(node).transitionDuration)).toBe('0s');
});

test('desktop three-position theme switch works with keyboard', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop');
  await page.goto('/en/');
  const theme = page.locator('.right-group [data-sves-theme-slider]');
  await expect(theme).toBeVisible();
  await theme.focus();
  await theme.press('End');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(theme).toHaveAttribute('aria-valuenow', '2');
  await theme.press('Home');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  // Synthetic keyboard clicks must not jump to the first slider position.
  await theme.press('Space');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('desktop sidebar and ToC can be hidden independently and restored', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop');
  await page.goto('/editorial-policy/');
  const sidebar = page.locator('button.panel-toggle[data-panel-toggle="sidebar"]');
  const toc = page.locator('button.panel-toggle[data-panel-toggle="toc"]');
  await expect(sidebar).toBeVisible();
  await expect(toc).toBeVisible();
  const pane = page.locator('.main-pane');
  const initialWidth = await pane.evaluate((element) => element.getBoundingClientRect().width);
  await sidebar.click();
  await expect(page.locator('html')).toHaveClass(/sves-hide-sidebar/);
  await expect.poll(() => pane.evaluate((element) => element.getBoundingClientRect().width))
    .toBeGreaterThan(initialWidth + 20);
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/sves-hide-sidebar/);
  const sidebarHiddenWidth = await pane.evaluate((element) => element.getBoundingClientRect().width);
  await toc.click();
  await expect(page.locator('html')).toHaveClass(/sves-hide-toc/);
  await expect(page.locator('.right-sidebar-container')).toBeHidden();
  await expect.poll(() => pane.evaluate((element) => element.getBoundingClientRect().width))
    .toBeGreaterThan(sidebarHiddenWidth + 20);
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/sves-hide-toc/);
  const restore = page.locator('button.toc-restore');
  await expect(restore).toBeVisible();
  await restore.click();
  await expect(page.locator('html')).not.toHaveClass(/sves-hide-toc/);
  await expect(page.locator('html')).toHaveClass(/sves-hide-sidebar/);
});

test('system appearance follows OS changes only in auto mode', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/ru/');
  const select = page.locator('[data-sves-mobile-toolbar] select[data-sves-theme-mobile]');
  await select.selectOption('auto');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await select.selectOption('dark');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});
