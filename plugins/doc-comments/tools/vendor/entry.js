// What the editor uses from CodeMirror 6 and Lezer, bundled once into
// page/vendor/codemirror.js by tools/vendor/build.sh. Nothing else is in it.
export { EditorState, EditorSelection, StateField, StateEffect, Compartment, Transaction, ChangeSet, Text, RangeSetBuilder, Prec, Facet, Annotation } from '@codemirror/state'
export { EditorView, Decoration, WidgetType, ViewPlugin, keymap, drawSelection, placeholder, highlightSpecialChars } from '@codemirror/view'
export { history, historyKeymap, defaultKeymap, undo, redo, indentMore, indentLess, insertNewlineAndIndent } from '@codemirror/commands'
export { Language, defineLanguageFacet, languageDataProp, syntaxTree, ensureSyntaxTree, LanguageSupport, indentUnit } from '@codemirror/language'
export { parser as markdownParser, GFM, Strikethrough, Table, TaskList } from '@lezer/markdown'
