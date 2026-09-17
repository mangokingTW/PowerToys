// Copyright (c) Microsoft Corporation
// The Microsoft Corporation licenses this file to you under the MIT license.
// See the LICENSE file in the project root for more information.

using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Microsoft.CmdPal.UI.ViewModels.Models;
using Microsoft.CommandPalette.Extensions;
using Microsoft.CommandPalette.Extensions.Toolkit;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace Microsoft.CmdPal.UI.ViewModels.UnitTests;

[TestClass]
public class ListItemViewModelTests
{
    private sealed class TestPageContext : IPageContext
    {
        public TaskScheduler Scheduler => TaskScheduler.Default;

        public ICommandProviderContext ProviderContext => CommandProviderContext.Empty;

        public void ShowException(Exception ex, string? extensionHint = null)
        {
            throw new AssertFailedException($"Unexpected exception from view model: {ex}");
        }
    }

    [TestMethod]
    public void AccessibleName_WithTitleAndSubtitle_ReturnsCombinedName()
    {
        var pageContext = new TestPageContext();
        var item = new ListItem(new NoOpCommand { Name = "Settings" })
        {
            Title = "Settings",
            Subtitle = "PowerToys Settings",
        };

        var viewModel = new ListItemViewModel(item, new(pageContext), DefaultContextMenuFactory.Instance);
        viewModel.InitializeProperties();

        Assert.AreEqual("Settings, PowerToys Settings", viewModel.AccessibleName);
    }

    [TestMethod]
    public void AccessibleName_WithTitleOnly_ReturnsTitleWithoutTrailingComma()
    {
        var pageContext = new TestPageContext();
        var item = new ListItem(new NoOpCommand { Name = "Calculator" })
        {
            Title = "Calculator",
            Subtitle = string.Empty,
        };

        var viewModel = new ListItemViewModel(item, new(pageContext), DefaultContextMenuFactory.Instance);
        viewModel.InitializeProperties();

        Assert.AreEqual("Calculator", viewModel.AccessibleName);
        Assert.IsFalse(viewModel.AccessibleName.EndsWith(", "));
    }

    [TestMethod]
    public void AccessibleName_WithSubtitleOnly_ReturnsSubtitle()
    {
        var pageContext = new TestPageContext();
        var item = new ListItem(new NoOpCommand { Name = string.Empty })
        {
            Title = string.Empty,
            Subtitle = "Description only",
        };

        var viewModel = new ListItemViewModel(item, new(pageContext), DefaultContextMenuFactory.Instance);
        viewModel.InitializeProperties();

        Assert.AreEqual("Description only", viewModel.AccessibleName);
    }

    [TestMethod]
    public void AccessibleName_WhenEmpty_ReturnsEmptyString()
    {
        var pageContext = new TestPageContext();
        var item = new ListItem(new NoOpCommand { Name = string.Empty })
        {
            Title = string.Empty,
            Subtitle = string.Empty,
        };

        var viewModel = new ListItemViewModel(item, new(pageContext), DefaultContextMenuFactory.Instance);
        viewModel.InitializeProperties();

        Assert.AreEqual(string.Empty, viewModel.AccessibleName);
    }

    [TestMethod]
    public void AccessibleName_WhenTitleOrSubtitleChanges_UpdatesAndRaisesPropertyChanged()
    {
        var pageContext = new TestPageContext();
        var item = new ListItem(new NoOpCommand { Name = "Initial Title" })
        {
            Title = "Initial Title",
            Subtitle = "Initial Subtitle",
        };

        var viewModel = new ListItemViewModel(item, new(pageContext), DefaultContextMenuFactory.Instance);
        viewModel.InitializeProperties();
        Assert.AreEqual("Initial Title, Initial Subtitle", viewModel.AccessibleName);

        var propertyChangedEvents = new List<string>();
        viewModel.PropertyChanged += (sender, args) =>
        {
            if (args.PropertyName != null)
            {
                propertyChangedEvents.Add(args.PropertyName);
            }
        };

        // Mutate Subtitle
        item.Subtitle = "Updated Subtitle";
        Assert.AreEqual("Initial Title, Updated Subtitle", viewModel.AccessibleName);
        CollectionAssert.Contains(propertyChangedEvents, nameof(viewModel.AccessibleName));

        propertyChangedEvents.Clear();

        // Mutate Title
        item.Title = "Updated Title";
        Assert.AreEqual("Updated Title, Updated Subtitle", viewModel.AccessibleName);
        CollectionAssert.Contains(propertyChangedEvents, nameof(viewModel.AccessibleName));
    }
}
