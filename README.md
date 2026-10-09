# bylinguals-jornal

Robô do **The Bylinguals Daily**, o jornal em inglês do Club do Portal Bylinguals.

- Todo dia às 3h17 (São Paulo), 9 robôs em paralelo — um por tema — escrevem 3 notícias dos Estados Unidos, 3 do mundo e 3 do Brasil.
- Modelo aberto (Qwen2.5 14B) rodando no processador do GitHub Actions: grátis, porque o repositório é público.
- Fontes: só o título e o resumo que cada jornal publica no próprio feed; a matéria da NASA inteira (domínio público). Nada é copiado: o texto é escrito do zero.
- Notícia completa só quando 3 ou mais veículos noticiaram o assunto (ou é da NASA); o resto vira nota curta de um veículo. Toda frase passa por uma conferência com as fontes.
- O Portal sabe que é este robô pelo token OIDC do GitHub (sem senha).

Testes manuais: aba **Actions → Jornal do Club → Run workflow** (`so_feeds` confere os feeds em 2 minutos; `seco` escreve sem publicar; o resultado vai para o ramo `testes`).
