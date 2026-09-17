// Copyright (c) Microsoft Corporation
// The Microsoft Corporation licenses this file to you under the MIT license.
// See the LICENSE file in the project root for more information.

using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.CmdPal.Ext.Bookmarks.Helpers;
using Microsoft.CmdPal.Ext.Bookmarks.Pages;
using Microsoft.CmdPal.Ext.Bookmarks.Persistence;
using Microsoft.CmdPal.Ext.Bookmarks.Services;
using Microsoft.CommandPalette.Extensions;
using Microsoft.CommandPalette.Extensions.Toolkit;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace Microsoft.CmdPal.Ext.Bookmarks.UnitTests;

[TestClass]
public class BookmarkPlaceholderPageTests
{
    private sealed class MockIconLocator : IBookmarkIconLocator
    {
        public Task<IIconInfo> GetIconForPath(Classification classification, CancellationToken cancellationToken = default)
        {
            return Task.FromResult<IIconInfo>(Icons.PinIcon);
        }
    }

    [TestMethod]
    public void ResetPlaceholderValues_ClearsAllPlaceholderRunsAndSubtitle()
    {
        // Arrange
        var placeholderParser = new PlaceholderParser();
        var resolver = new BookmarkResolver(placeholderParser);
        var iconLocator = new MockIconLocator();
        var bookmarkData = new BookmarkData(Guid.NewGuid(), "Ticket", "https://example.com/{group}/{id}");

        using var page = new BookmarkPlaceholderPage(bookmarkData, iconLocator, resolver, placeholderParser);

        var placeholderRuns = page.Parameters.OfType<StringParameterRun>().ToList();
        Assert.AreEqual(2, placeholderRuns.Count);

        // Pre-fill values
        placeholderRuns[0].Text = "issues";
        placeholderRuns[1].Text = "50000";

        Assert.IsFalse(placeholderRuns[0].NeedsValue);
        Assert.IsFalse(placeholderRuns[1].NeedsValue);
        Assert.AreEqual("https://example.com/issues/50000", page.Command.Subtitle);

        // Act
        page.ResetPlaceholderValues();

        // Assert
        Assert.AreEqual(string.Empty, placeholderRuns[0].Text);
        Assert.AreEqual(string.Empty, placeholderRuns[1].Text);
        Assert.IsTrue(placeholderRuns[0].NeedsValue);
        Assert.IsTrue(placeholderRuns[1].NeedsValue);
        Assert.AreEqual(string.Empty, page.Command.Subtitle);
    }

    [TestMethod]
    public void ResetPlaceholderValues_WhenAlreadyEmpty_DoesNotThrow()
    {
        // Arrange
        var placeholderParser = new PlaceholderParser();
        var resolver = new BookmarkResolver(placeholderParser);
        var iconLocator = new MockIconLocator();
        var bookmarkData = new BookmarkData(Guid.NewGuid(), "Ticket", "https://example.com/{ticket}");

        using var page = new BookmarkPlaceholderPage(bookmarkData, iconLocator, resolver, placeholderParser);

        var placeholderRun = page.Parameters.OfType<StringParameterRun>().Single();
        Assert.AreEqual(string.Empty, placeholderRun.Text);

        // Act & Assert
        page.ResetPlaceholderValues();
        Assert.AreEqual(string.Empty, placeholderRun.Text);
        Assert.IsTrue(placeholderRun.NeedsValue);
    }

    [TestMethod]
    public void ResetPlaceholderValues_WithDuplicatePlaceholders_ClearsSharedRun()
    {
        // Arrange
        var placeholderParser = new PlaceholderParser();
        var resolver = new BookmarkResolver(placeholderParser);
        var iconLocator = new MockIconLocator();
        var bookmarkData = new BookmarkData(Guid.NewGuid(), "Repeated", "https://example.com/{id}/{id}");

        using var page = new BookmarkPlaceholderPage(bookmarkData, iconLocator, resolver, placeholderParser);
        var runs = page.Parameters.OfType<StringParameterRun>().ToList();
        Assert.AreEqual(2, runs.Count);
        Assert.AreSame(runs[0], runs[1]);

        runs[0].Text = "42";
        Assert.AreEqual("https://example.com/42/42", page.Command.Subtitle);

        // Act
        page.ResetPlaceholderValues();

        // Assert
        Assert.AreEqual(string.Empty, runs[0].Text);
        Assert.AreEqual(string.Empty, runs[1].Text);
        Assert.IsTrue(runs[0].NeedsValue);
        Assert.AreEqual(string.Empty, page.Command.Subtitle);
    }

    [TestMethod]
    public void Launch_WhenPlaceholdersMissingValues_KeepsOpen()
    {
        // Arrange
        var placeholderParser = new PlaceholderParser();
        var resolver = new BookmarkResolver(placeholderParser);
        var iconLocator = new MockIconLocator();
        var bookmarkData = new BookmarkData(Guid.NewGuid(), "Ticket", "https://example.com/{group}/{id}");

        using var page = new BookmarkPlaceholderPage(bookmarkData, iconLocator, resolver, placeholderParser);
        var runs = page.Parameters.OfType<StringParameterRun>().ToList();
        runs[0].Text = "issues"; // only first placeholder has value, second is empty

        // Act
        var result = page.Command.Command.Invoke();

        // Assert
        Assert.IsNotNull(result);
        Assert.AreEqual(CommandResultKind.KeepOpen, result.Kind);
        Assert.AreEqual("issues", runs[0].Text);
        Assert.AreEqual(string.Empty, runs[1].Text);
    }
}
