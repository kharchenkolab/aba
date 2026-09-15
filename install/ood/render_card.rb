#!/usr/bin/env ruby
# Render an OOD card template the way OnDemand does, and print the result as JSON.
#
#   ruby render_card.rb form   <form.yml.erb>
#   ruby render_card.rb submit <submit.yml.erb> '<json of form values>'
#
# OnDemand is the only thing that renders these files in production, and it does
# so on the web host, AFTER a deploy has published them — so a template that
# raises, or renders YAML that does not parse, is found by the first person to
# open the form. This renders them first: ERB with trim_mode '-', then
# YAML.safe_load, as OnDemand's BatchConnect::App does. submit.yml.erb gets the
# form values as an OpenStruct binding, which is how its attributes resolve as
# bare names (`defined?(aba_lab)`).
#
# Exit: 0 rendered (JSON on stdout)
#       1 the template raised — for submit.yml.erb, how a launch is REFUSED
#         (OnDemand shows the message on the form and submits nothing)
#       2 it rendered, but the output is not YAML
#       3 the template itself is broken (Ruby syntax) — never a refusal
#      64 usage
require 'erb'
require 'yaml'
require 'json'
require 'ostruct'

kind, path, values = ARGV
unless %w[form submit].include?(kind) && path && File.file?(path)
  warn 'usage: render_card.rb form|submit <template> [json-values]'
  exit 64
end

ctx = kind == 'submit' ? OpenStruct.new(JSON.parse(values || '{}')) : Object.new
begin
  erb = ERB.new(File.read(path), trim_mode: '-')
  erb.filename = path
  text = erb.result(ctx.instance_eval { binding })
rescue ScriptError => e
  warn "#{path}: broken template: #{e.message}"
  exit 3
rescue StandardError => e
  warn e.message
  exit 1
end

begin
  puts JSON.generate(YAML.safe_load(text))
rescue Psych::Exception => e
  warn "#{path}: rendered, but not YAML: #{e.message}"
  exit 2
end
