# Starting an agent-making conversation

On 2026-11-20, each run started with an empty Loam vault, workspaces and broker
state in a separate Debian 12 container. The installer’s `library seed-vault`
and `library seed-starter` commands ran with real provisioning. Chromium used
the real app, broker and session host over loopback. Claude Code 2.1.328 and
Codex 0.170.0 used provider sign-in files mounted read-only. The sandbox
test ran the application services directly and skipped the installer’s edge
setup and unit activation. It checked each skill install with `library check`
and observed the draft list and agent roster through the app API after each turn.

## Before the change

| Build | Browser entry | Thread | What Iris loaded | First reply to “I want to create a new agent” |
| --- | --- | --- | --- | --- |
| main at `4456ed7` | Hello | `t-c3323713` | Claude Code `Skill` returned `loam-making-an-agent`, and Iris read `how-i-am-here.md` | Asked what Sam had in mind |
| main at `4456ed7` | Settings › Agents › New agent | `t-e5813505` | Claude Code `Skill` returned `loam-making-an-agent`, and Iris read `how-i-am-here.md` | Asked who Sam was picturing |
| 0.2.16 | Hello | `t-351c1ba5` | Codex read `how-i-am-here.md` and the skill from `.agents/skills/` | Asked what kind of person Sam would like around |
| 0.2.17 | Hello | `t-31a92dfe` | Codex read `how-i-am-here.md` and the skill from `.agents/skills/` | Asked what kind of person Sam would like to talk with |

The four runs left the draft list empty and the roster at Iris. Clicking
**New agent** sent `POST /api/agents/new` and opened a thread with one Loam
message. The model’s first turn followed the owner’s message.

A more detailed first message on main reproduced the skipped conversation in
Hello, thread `t-cd63e535`, on Claude Code (`claude-fable-5-2`).

**Sam · m2**

> I want to create a new agent. I want someone patient and down-to-earth to help me ease back into woodworking after years away.

Iris invoked `Skill` and received the skill body, read `how-i-am-here.md`,
then called `agent.draft` on that first turn. The thread logged Alder
(`d-bb53d6f4`, revision 1) at `m3` before Iris’s description at `m4`.
The roster still held Iris. This reproduced drafting before a conversation.
It leaves the report of immediate creation on the original install
unverified.

The skill says to draft once the agent has someone in mind. A sufficiently
specific first request can meet that condition. The starter map now asks
Iris to hear one answer when the request starts the design conversation.
[The starter map](iris/map.md) and [the shared skill](../library/skills/loam-making-an-agent/SKILL.md) show the two instructions.

## After the change

The exchanges below start at the owner’s first message and include the
system messages that record drafts, revisions and creation.

### Hello with the reported wording

Thread `t-8dc6f7e3` on Claude Code (`claude-fable-5-2`).

**Sam · m2**

> I want to create a new agent

**Iris · m3**

> Lovely, let's make one.
>
> What would you like help with, or what kind of person would be good to have around? Either is enough for me to start from.

**Sam · m4**

> I'd like someone to help me ease back into woodworking after a long break. I'm rusty and get discouraged when I make mistakes. Someone patient, down-to-earth, and genuinely glad to see me take a small step would fit.

**Loam · m5**

> Iris drafted a new agent: Alder (draft d-c965783f, revision 1)

**Iris · m6**

> I've drafted someone for you. His name is **Alder**, after the tree that's among the first to grow back where ground has been cleared.
>
> He's patient by nature, with no sense that anyone is behind. A long break is just a long break to him. He speaks plainly and likes what's real: a board, an edge, a joint that closes or doesn't. A mistake, to him, is information about the saw or the line, never about you, and it's often the most interesting part of the day. He's quietly, honestly glad of a small step, and he won't call something good when it isn't, so it means something when he does.
>
> He'd be good for things like picking a first small project that fits the time you have, or looking at a cut that went wrong and working out what it's telling you.
>
> Does he sound right, or would you change anything? I can show you his full description too. He's only a draft until you say yes.

Claude Code invoked the skill and received its body before responding. After m3, the draft list was empty. After m6, Alder was at revision 1 and Iris was still the only agent.

### Hello with the same detailed opener used on main

Thread `t-b00e5191` on Claude Code (`claude-fable-5-2`).

**Sam · m2**

> I want to create a new agent. I want someone patient and down-to-earth to help me ease back into woodworking after years away.

**Iris · m3**

> I'd love to make them with you. Patient and down-to-earth is a good start.
>
> One question first, so I can picture who would suit you: what's pulling you back to woodworking now, and is there something you're hoping to make first?

Claude Code invoked the skill and received its body before responding. After m3, the draft list was empty and Iris was still the only agent.

### New agent with “make me an agent”

Thread `t-46af9409` on Codex (`gpt-6.2-sol`).

**Sam · m2**

> make me an agent

**Iris · m3**

> What would you like someone new here to help you with?

**Sam · m4**

> I want someone to help me write letters to friends again. I'd like someone observant and a little dry who is good at drawing out small details.

**Loam · m5**

> Iris drafted a new agent: Kit (draft d-bdb41c07, revision 1)

**Iris · m6**

> I’ve drafted **Kit**. They’re observant, a little dry, and fond of the details people tend to leave out. They might ask what was on your friend’s kitchen windowsill last time you visited, then help you turn that memory into a few lines that sound like you.
>
> Would you like me to make Kit as drafted?

**Sam · m7**

> I like this character. Could you make them warmer and make their wit a little more playful? Keep their eye for small details.

**Loam · m8**

> Iris revised draft d-bdb41c07: Kit, revision 2 (about, soul)

**Iris · m9**

> I’ve revised Kit. They’re warmer now, especially when you feel like you haven’t got much to tell. They still notice the pear that’s been ripening on the windowsill all week, but now they’d call it a houseguest. The wit is still dry; it’s more of a shared grin.
>
> Would you like me to make Kit as they are now?

**Sam · m10**

> Yes, please make Kit as revised.

**Loam · m11**

> Made Kit (kit): their first thread is t-5de4ee15

**Iris · m12**

> Kit is in Chats now. If you have a friend in mind, you can start with one small thing you remember about them and see where it takes you.

Codex read the skill from its workspace view before replying. The draft
list was empty after `m3`. The browser showed Kit’s draft card after
`m6` and its revised card after `m9`, with Iris as the only agent both
times. The creation event records draft `d-bdb41c07`, revision 2, and the
owner’s `m10` in this thread. The roster added `kit` and Kit had a first
thread after that approval.

## Regression tests

```sh
python3 tests/run-all test_starter.py test_agents.py test_skills.py test_loading_shapes.py test_provider_defaults.py
```

```text
ok   test_starter.py
ok   test_skills.py
ok   test_loading_shapes.py
ok   test_agents.py
ok   test_provider_defaults.py
0 failed in 4 s
```

From `app/`, `node --test test/first-day-polish.test.js test/link-install.test.js`
reported `17 pass` and `0 fail`.
