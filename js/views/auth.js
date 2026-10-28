/**
 * Sign in and sign up.
 *
 * Two fields plus a name. Anything else the product needs about a student it
 * learns from how they answer questions, not from a form.
 */

import { h, icon, focus } from '../core/dom.js';
import { api } from '../core/api.js';
import { store } from '../core/store.js';
import { navigate } from '../core/router.js';
import { brand } from '../components/shell.js';
import { resonanceFigure } from '../core/resonance.js';
import { dotField } from '../components/dotfield.js';

const DEMO = { email: 'student@aloud.app', password: 'learnaloud' };

function aside() {
  return h(
    'aside',
    { class: 'auth-aside night' },
    // Behind the copy, not competing with it.
    dotField({ color: 'rgb(236,238,244)', opacity: 0.16 }),
    resonanceFigure('aloud-auth', { active: false }),
    h('h2', null, 'Listen. Think. Answer. Learn.'),
    h(
      'p',
      null,
      'Your own material, taught out loud by a teacher who asks what you made of it ' +
        'and changes course when the answer says you are not there yet.'
    )
  );
}

function formShell({ title, subtitle, children, footer }) {
  return h(
    'div',
    { class: 'auth-page' },
    aside(),
    h(
      'main',
      { class: 'auth-main', id: 'main' },
      h(
        'div',
        { class: 'auth-form stack stack-lg' },
        h('div', { style: { marginBottom: 'var(--space-2)' } }, brand()),
        h('div', null, h('h1', null, title), h('p', { class: 'muted' }, subtitle)),
        children,
        footer
      )
    )
  );
}

function errorNotice(message) {
  return h('div', { class: 'notice notice-trouble', role: 'alert' }, icon('warn', { size: 16 }), message);
}

async function afterAuth(result) {
  store.set({ user: result.user });
  const me = await api.auth.me().catch(() => null);
  if (me) store.set({ user: me.user, providers: me.providers });

  const { spaces } = await api.spaces.list().catch(() => ({ spaces: [] }));
  store.set({ spaces });
  // Someone with no subjects has nothing to look at on a dashboard.
  navigate(spaces.length ? '/app' : '/welcome', { replace: true });
}

export function signInView() {
  let email = null;
  let password = null;
  let errorSlot = null;
  let submit = null;

  async function onSubmit(event) {
    event.preventDefault();
    errorSlot.replaceChildren();
    submit.disabled = true;
    submit.textContent = 'Signing in...';
    try {
      const result = await api.auth.signIn({
        email: email.value.trim(),
        password: password.value,
      });
      await afterAuth(result);
    } catch (error) {
      errorSlot.replaceChildren(errorNotice(error.message));
      submit.disabled = false;
      submit.textContent = 'Sign in';
      focus(password);
    }
  }

  function fillDemo() {
    email.value = DEMO.email;
    password.value = DEMO.password;
    focus(submit);
  }

  return formShell({
    title: 'Welcome back',
    subtitle: 'Pick up wherever you stopped.',
    children: h(
      'form',
      { class: 'stack', onSubmit, novalidate: true },
      h('div', { ref: (el) => { errorSlot = el; } }),
      h(
        'label',
        { class: 'field' },
        h('span', { class: 'field-label' }, 'Email'),
        h('input', {
          class: 'input', type: 'email', name: 'email', autocomplete: 'email',
          required: true, placeholder: 'you@school.edu',
          ref: (el) => { email = el; },
        })
      ),
      h(
        'label',
        { class: 'field' },
        h('span', { class: 'field-label' }, 'Password'),
        h('input', {
          class: 'input', type: 'password', name: 'password',
          autocomplete: 'current-password', required: true,
          ref: (el) => { password = el; },
        })
      ),
      h(
        'button',
        { class: 'btn btn-primary btn-lg btn-block', type: 'submit', ref: (el) => { submit = el; } },
        'Sign in'
      ),
      h(
        'button',
        { class: 'btn btn-ghost btn-block', type: 'button', onClick: fillDemo },
        icon('sparkle', { size: 15 }),
        'Use the sample subject'
      )
    ),
    footer: h(
      'p',
      { class: 'meta', style: { textAlign: 'center' } },
      'No account yet? ',
      h('a', { href: '/signup', class: 'strong' }, 'Create one')
    ),
  });
}

export function signUpView() {
  let name = null;
  let email = null;
  let password = null;
  let errorSlot = null;
  let submit = null;

  async function onSubmit(event) {
    event.preventDefault();
    errorSlot.replaceChildren();

    if (password.value.length < 8) {
      errorSlot.replaceChildren(errorNotice('Passwords need at least 8 characters.'));
      focus(password);
      return;
    }

    submit.disabled = true;
    submit.textContent = 'Creating your account...';
    try {
      const result = await api.auth.signUp({
        email: email.value.trim(),
        password: password.value,
        displayName: name.value.trim(),
      });
      await afterAuth(result);
    } catch (error) {
      errorSlot.replaceChildren(errorNotice(error.message));
      submit.disabled = false;
      submit.textContent = 'Create account';
    }
  }

  return formShell({
    title: 'Start learning aloud',
    subtitle: 'You bring the material. It becomes a course.',
    children: h(
      'form',
      { class: 'stack', onSubmit, novalidate: true },
      h('div', { ref: (el) => { errorSlot = el; } }),
      h(
        'label',
        { class: 'field' },
        h('span', { class: 'field-label' }, 'What should the teacher call you?'),
        h('input', {
          class: 'input', type: 'text', name: 'name', autocomplete: 'given-name',
          placeholder: 'Sam', maxlength: '80',
          ref: (el) => { name = el; },
        })
      ),
      h(
        'label',
        { class: 'field' },
        h('span', { class: 'field-label' }, 'Email'),
        h('input', {
          class: 'input', type: 'email', name: 'email', autocomplete: 'email',
          required: true, placeholder: 'you@school.edu',
          ref: (el) => { email = el; },
        })
      ),
      h(
        'label',
        { class: 'field' },
        h('span', { class: 'field-label' }, 'Password'),
        h('input', {
          class: 'input', type: 'password', name: 'password',
          autocomplete: 'new-password', required: true, minlength: '8',
          ref: (el) => { password = el; },
        }),
        h('span', { class: 'field-hint' }, 'At least 8 characters.')
      ),
      h(
        'button',
        { class: 'btn btn-primary btn-lg btn-block', type: 'submit', ref: (el) => { submit = el; } },
        'Create account'
      )
    ),
    footer: h(
      'p',
      { class: 'meta', style: { textAlign: 'center' } },
      'Already have an account? ',
      h('a', { href: '/signin', class: 'strong' }, 'Sign in')
    ),
  });
}
